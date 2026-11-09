"""Common schedules, explicit abstention, and exposure-matched comparisons."""
from dataclasses import replace
from pathlib import Path
import numpy as np
from .retail import Policy,simulate
from .compare_baselines import candidates
from .forecast_outcomes import outcome
from .forward_tracking import atomic_json

SCENARIOS={'base':(10,10,0),'double_costs':(20,20,0),'entry_delayed':(10,10,1)}


def schedule(days,sessions,start,end,horizon,lag=0):
    index={d:i for i,d in enumerate(sessions)};result=[];next_entry=-1
    for day in sorted(days):
        entry=index[day]+1+lag;exit_index=entry+horizon
        if entry<next_entry or exit_index>=len(sessions):continue
        if sessions[entry]<start or sessions[exit_index]>end:continue
        result.append((day,entry));next_entry=exit_index+1
    return result


def select_rows(pool,strategy,cutoff=.01):
    rows=list(pool)
    if strategy.endswith('_selective'):
        rows=[r for r in rows if r['absolute_score']>cutoff and (not strategy.startswith('relative') or r['relative_score']>0)]
    field='relative_score' if strategy.startswith('relative') else 'absolute_score' if strategy.startswith('absolute') else 'momentum' if strategy=='momentum' else 'current_score'
    return [{**r,'rank':i} for i,r in enumerate(sorted(rows,key=lambda r:(-r[field],r['symbol'])),1)]


def basket(rows,entry,bars,sessions,events,policy):
    selected=rows[:5];values=[outcome(r['symbol'],entry,policy.horizon,bars,sessions,events,policy) for r in selected]
    known=all(v['status']=='complete' for v in values)
    # Each position has one fifth of the basket capital; unused slots earn zero.
    return {'status':'complete' if known else 'incomplete','selected_symbols':[r['symbol'] for r in selected],
        'selected_count':len(selected),'exposure':len(selected)/5,
        'net_return':sum(v['net_return'] for v in values)/5 if known else None,'outcomes':values}


def paired(a,b):
    if [r['signal_date'] for r in a]!=[r['signal_date'] for r in b]:raise ValueError('Unpaired scheduled cycles')
    differences=[x['net_return']-y['net_return'] for x,y in zip(a,b) if x['status']==y['status']=='complete']
    active=[x['net_return']-y['net_return'] for x,y in zip(a,b) if x['selected_count'] and x['status']==y['status']=='complete']
    rng=np.random.default_rng(42)
    interval=np.quantile(rng.choice(differences,(2000,len(differences))).mean(axis=1),[.025,.975]).tolist() if differences else None
    return {'scheduled_cycles':len(a),'complete_pairs':len(differences),'active_complete_pairs':len(active),
        'coverage':len(differences)/len(a) if a else 0,'mean_difference':float(np.mean(differences)) if differences else None,
        'mean_active_difference':float(np.mean(active)) if active else None,'descriptive_95pct_interval':interval}


def cycle_summary(cycles):
    complete=bool(cycles) and all(r['status']=='complete' for r in cycles)
    equity=1.;peak=1.;dd=0.
    for row in cycles:
        if row['status']!='complete':continue
        equity*=1+row['net_return'];peak=max(peak,equity);dd=min(dd,equity/peak-1)
    return {'status':'complete' if complete else 'incomplete','scheduled_cycles':len(cycles),
        'active_cycles':sum(r['selected_count']>0 for r in cycles),'net_return':equity-1 if complete else None,
        'cycle_close_drawdown':dd if complete else None,'mean_cycle_return':float(np.mean([r['net_return'] for r in cycles])) if complete else None,
        'mean_exposure':float(np.mean([r['exposure'] for r in cycles])) if cycles else None}


def evaluate(prediction,statistics,bars,sessions,events,start,end,horizon,output=None):
    names=['absolute_always','absolute_selective','relative_always','relative_selective','momentum']
    if 'current_score' in prediction:names.append('current_model')
    ranked={n:{} for n in names};pool_sizes=[]
    for day,group in prediction.groupby('trade_date',sort=True):
        records=group.assign(score=group.absolute_score).to_dict('records')
        pool=candidates(records,statistics,day,Policy(horizon=horizon),events);pool_sizes.append(len(pool))
        for name in names:ranked[name][day]=select_rows(pool,name)
    results={name:{} for name in names};controls=['absolute_always','momentum']+(['current_model'] if 'current_model' in names else [])
    calendar={d:i for i,d in enumerate(sessions)}
    for scenario,(fee,slip,lag) in SCENARIOS.items():
        policy=Policy(horizon=horizon,fee_bps=fee,slippage_bps=slip)
        cycles={n:[] for n in names};matched={n:{c:[] for c in controls if c!=n} for n in names}
        for day,entry in schedule(prediction.trade_date.unique(),sessions,start,end,horizon,lag):
            for name in names:
                result={'signal_date':day,'entry_date':sessions[entry],'exit_date':sessions[entry+horizon],**basket(ranked[name][day],entry,bars,sessions,events,policy)}
                cycles[name].append(result)
                for control in matched[name]:
                    count=result['selected_count']
                    rows=ranked[control][day][:count]
                    matched[name][control].append({'signal_date':day,**basket(rows,entry,bars,sessions,events,policy)})
        for name in names:
            signals=ranked[name]
            if lag:signals={sessions[calendar[d]+lag]:rows for d,rows in signals.items() if calendar[d]+lag<len(sessions)}
            ledger=simulate(signals,bars,sessions,policy,events,start,end)
            if output:
                atomic_json(Path(output)/f'{name}_{scenario}_retail.json',ledger)
            book={k:ledger[k] for k in ('status','blocked','net_return','max_drawdown','trade_win_rate')}
            book.update(closed_trades=len(ledger['trades']),open_positions=len(ledger['open_positions']),
                mean_cash_fraction=float(np.mean([p['cash']/p['equity'] for p in ledger['curve']])) if ledger['curve'] else None)
            results[name][scenario]={'basket_summary':cycle_summary(cycles[name]),'cycles':cycles[name],
                'retail':book,'comparisons':{c:paired(cycles[name],cycles[c]) for c in controls if c!=name},
                'exposure_matched_comparisons':{c:paired(cycles[name],v) for c,v in matched[name].items()},
                'abstention_date_fraction':sum(not rows for rows in ranked[name].values())/len(ranked[name]) if ranked[name] else None,
                'matched_control_cycles':matched[name]}
    return {'start':start,'end':end,'horizon':horizon,'scored_dates':prediction.trade_date.nunique(),
        'pool_min':min(pool_sizes,default=0),'pool_mean':float(np.mean(pool_sizes)) if pool_sizes else None,'strategies':results}


def qualify(result,folds,name):
    reasons=[];candidate=result['strategies'][name]
    controls=['absolute_always','momentum']+(['current_model'] if 'current_model' in result['strategies'] else [])
    for scenario in SCENARIOS:
        row=candidate[scenario];summary=row['basket_summary'];retail=row['retail']
        if summary['status']!='complete' or summary['active_cycles']<8:reasons.append(f'{scenario}: fewer than eight complete active cycles')
        if summary['net_return'] is None or summary['net_return']<=0:reasons.append(f'{scenario}: nonpositive basket return')
        if retail['status']!='complete' or retail['net_return'] is None or retail['net_return']<=0:reasons.append(f'{scenario}: incomplete or nonpositive retail equity return')
        for control in controls:
            for kind in ('comparisons','exposure_matched_comparisons'):
                comparison=row[kind][control]
                if comparison['coverage']<1 or comparison['mean_difference'] is None or comparison['mean_difference']<=0:reasons.append(f'{scenario}: no complete positive {kind} advantage over {control}')
            baseline=result['strategies'][control][scenario]['retail']
            if baseline['status']!='complete' or retail['status']!='complete' or retail['net_return'] is None or baseline['net_return'] is None or retail['net_return']<=baseline['net_return']:reasons.append(f'{scenario}: no retail advantage over {control}')
    for control in controls:
        positive=sum(f['strategies'][name]['base']['comparisons'][control]['mean_difference'] is not None and f['strategies'][name]['base']['comparisons'][control]['mean_difference']>0 for f in folds)
        if positive<2:reasons.append(f'Positive basket advantage over {control} in fewer than two validation folds')
    book=candidate['base']['retail']
    if book['closed_trades']<20 or book['max_drawdown'] is None or book['max_drawdown']<-.25:reasons.append('Trade count/drawdown gate failed')
    return reasons
