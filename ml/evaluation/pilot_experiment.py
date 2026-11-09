"""Frozen 20-issuer information ablation; historical research only.

New output directory; never changes the existing prospective trial or production.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.pilot_coverage import require_ready,CONFIG
from ml.evaluation.model_tournament import prepare,sha,TECHNICAL_FEATURES
from ml.evaluation.information import attach_earnings,sector_features,EARNINGS_FEATURES,SECTOR_FEATURES
from ml.evaluation.information_model import InformationModel
from ml.evaluation.tournament_models import training_rows
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.compare_baselines import candidates
from ml.evaluation.forecast_outcomes import outcome
from ml.evaluation.corporate_actions import load_events
from ml.evaluation.retail import Policy,simulate

ARMS={'technical':TECHNICAL_FEATURES,'sector':TECHNICAL_FEATURES+SECTOR_FEATURES,
      'financial':TECHNICAL_FEATURES+SECTOR_FEATURES+EARNINGS_FEATURES}
SCENARIOS={'base':(Policy(),0),'double_costs':(Policy(fee_bps=20,slippage_bps=20),0),'entry_delayed':(Policy(),1)}


def cycle_schedule(days,sessions,start,end,lag=0):
    positions={d:i for i,d in enumerate(sessions)}
    schedule=[];next_entry=-1
    for day in sorted(days):
        entry=positions[day]+1+lag
        exit_index=entry+21
        if entry<next_entry or exit_index>=len(sessions):continue
        if sessions[entry]<start or sessions[exit_index]>end:continue
        schedule.append((day,entry));next_entry=exit_index+1
    return schedule


def paired(a,b):
    if [r['signal_date'] for r in a]!=[r['signal_date'] for r in b]:raise ValueError('Unpaired schedules')
    values=[x['net_return']-y['net_return'] for x,y in zip(a,b) if x['status']==y['status']=='complete']
    rng=np.random.default_rng(42)
    interval=np.quantile([np.mean(rng.choice(values,len(values))) for _ in range(2000)],[.025,.975]).tolist() if values else None
    return {'scheduled_pairs':len(a),'complete_pairs':len(values),'coverage':len(values)/len(a) if a else 0,
            'mean_difference':float(np.mean(values)) if values else None,'descriptive_95pct_interval':interval,
            'warning':'Descriptive, not multiplicity-adjusted evidence or a fresh holdout'}


def score_quality(prediction,frame,end):
    data=prediction.merge(frame[['symbol','trade_date','net_return','scheduled_exit_date']],validate='one_to_one')
    data=data[data.net_return.notna() & data.scheduled_exit_date.notna() & (data.scheduled_exit_date<=end)]
    values=[]
    for _,p in data.groupby('trade_date'):
        if len(p)>=5 and p.score.nunique()>1 and p.net_return.nunique()>1:
            v=p.score.corr(p.net_return,method='spearman')
            if np.isfinite(v):values.append(float(v))
    return {'mean_daily_rank_ic':float(np.mean(values)) if values else None,'evaluated_dates':len(values),'known_rows':len(data)}


def evaluate(scores,statistics,bars,sessions,events,start,end):
    # Same row keys and signal-time eligibility for all arms, never label based.
    names=list(scores);groups={n:{d:p for d,p in s.groupby('trade_date')} for n,s in scores.items()}
    days=sorted(set(groups[names[0]]))
    for n in names[1:]:
        if set(groups[n])!=set(days):raise ValueError('Unmatched signal dates')
    ranked={n:{} for n in names}
    eligible_counts=[]
    for day in days:
        keys=[set(groups[n][day].symbol) for n in names]
        if any(k!=keys[0] for k in keys):raise ValueError('Unmatched signal stocks')
        for n in names:
            pool=candidates(groups[n][day].to_dict('records'),statistics,day,Policy(),events)
            ranked[n][day]=[{**r,'rank':i} for i,r in enumerate(sorted(pool,key=lambda r:(-r['score'],r['symbol'])),1)]
        eligible_counts.append(len(ranked[names[0]][day]))
    results={n:{'scenarios':{}} for n in names}
    positions={d:i for i,d in enumerate(sessions)}
    for scenario,(policy,lag) in SCENARIOS.items():
        schedule=cycle_schedule(days,sessions,start,end,lag)
        for n in names:
            cycles=[]
            for day,entry in schedule:
                selected=ranked[n][day][:5]
                outcomes=[outcome(r['symbol'],entry,21,bars,sessions,events,policy) for r in selected]
                known=all(v['status']=='complete' for v in outcomes)
                cycles.append({'signal_date':day,'entry_date':sessions[entry],'scheduled_exit_date':sessions[entry+21],
                    'selected_symbols':[r['symbol'] for r in selected],'status':'complete' if known else 'incomplete',
                    'net_return':float(np.mean([v['net_return'] for v in outcomes])) if known and outcomes else 0. if known else None,
                    'outcomes':outcomes})
            signals=ranked[n]
            if lag:signals={sessions[positions[d]+lag]:rows for d,rows in signals.items() if positions[d]+lag<len(sessions)}
            book=simulate(signals,bars,sessions,policy,events,start,end)
            summary={k:book[k] for k in ('status','blocked','net_return','max_drawdown','trade_win_rate')}
            summary.update(closed_trades=len(book['trades']),open_positions=len(book['open_positions']),
                mean_cash_fraction=float(np.mean([c['cash']/c['equity'] for c in book['curve']])) if book['curve'] else None,
                valuation='Retail final equity includes marked open positions; independent baskets require scheduled maturity')
            results[n]['scenarios'][scenario]={'top5_cycles':cycles,'retail':summary}
    for n in names:
        results[n]['comparisons']={control:{scenario:paired(results[n]['scenarios'][scenario]['top5_cycles'],results[control]['scenarios'][scenario]['top5_cycles']) for scenario in SCENARIOS}
                                  for control in names if control!=n}
    return {'strategies':results,'signal_dates':len(days),'eligible_pool_min':min(eligible_counts,default=0),
            'eligible_pool_mean':float(np.mean(eligible_counts)) if eligible_counts else None}


def selection_reasons(validation,fold_results):
    """Fixed financial-versus-sector decision; no model or hyperparameter search."""
    reasons=[];financial=validation['strategies']['financial']
    for control in ('sector','momentum','current_model'):
        for scenario in SCENARIOS:
            pair=financial['comparisons'][control][scenario]
            a=financial['scenarios'][scenario]['retail'];b=validation['strategies'][control]['scenarios'][scenario]['retail']
            if pair['coverage']<1 or pair['complete_pairs']<8 or pair['mean_difference'] is None or pair['mean_difference']<=0:
                reasons.append(f'No complete positive top-five advantage over {control}/{scenario}')
            if a['status']!='complete' or b['status']!='complete' or a['net_return']<=max(0,b['net_return']):
                reasons.append(f'No complete positive retail advantage over {control}/{scenario}')
        positive=sum(r['strategies']['financial']['comparisons'][control]['base']['mean_difference'] is not None and r['strategies']['financial']['comparisons'][control]['base']['mean_difference']>0 for r in fold_results)
        if positive<2:reasons.append(f'Financial top-five advantage over {control} in fewer than two validation folds')
    base=financial['scenarios']['base']['retail']
    if base['closed_trades']<20 or base['max_drawdown'] is None or base['max_drawdown']<-.25:reasons.append('Trade count or drawdown gate failed')
    return reasons


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',type=Path,default=ROOT/'ml/evaluation/results/financial_pilot_v1')
    parser.add_argument('--base',type=Path,default=ROOT/'ml/evaluation/results/model_tournament_20261108')
    parser.add_argument('--output',type=Path,default=ROOT/'ml/evaluation/results/verified_pilot_experiment_20261109')
    args=parser.parse_args();report=require_ready(args.pilot);args.output.mkdir(parents=True,exist_ok=True)
    bp=json.loads((args.base/'protocol.json').read_text());reference=Path(bp['reference_directory']);inputs=Path(bp['input_directory'])
    refs=[];folds={}
    for f in range(1,6):
        path=reference/f'rank21_fold{f}.csv'
        if sha(path)!=bp['reference_files'][path.name]:raise RuntimeError('Reference changed')
        p=pd.read_csv(path);p['fold']=f;refs.append(p);folds[f]={'start':p.trade_date.min(),'end':p.trade_date.max()}
    sources=('pilot_experiment.py','pilot_coverage.py','financial_reconciliation.py','reconcile_pilot.py','information.py','information_model.py',
             'model_tournament.py','tournament_models.py','retail.py','compare_baselines.py','forecast_outcomes.py','corporate_actions.py')
    protocol={'version':1,'pilot_config_sha256':sha(CONFIG),'coverage_report_sha256':sha(args.pilot/'coverage_report.json'),
        'input_manifest_sha256':sha(inputs/'manifest.json'),'base_protocol_sha256':sha(args.base/'protocol.json'),
        'source_hashes':{n:sha(ROOT/'ml/evaluation'/n) for n in sources},'arms':ARMS,'folds':folds,
        'validation_folds':[1,2,3],'later_assessment_folds':[4,5],'embargo_sessions':63,'seed':42,'tuning_trials':0,
        'model':'Existing fixed InformationModel: LightGBM7 leaves100 trees; train-only median imputation with missing indicators',
        'training':'Frozen20-stock universe, dates from2022-01-01; next-close21-session net outcome must mature before validation; no target filtering of test rows',
        'features':'Sector context computed in full original universe then restricted to pilot; historical verified filings publication+next day, not contemporaneous observation',
        'evaluation':'Same scored row pool across five strategies. Equal-notional top5, deterministic nonoverlapping21-session cycles; actual ten-position retail planner; base, double-cost and one-session-delay scenarios',
        'selection':'Financial must beat sector, momentum and current model in complete top5 cycles (>=8) and positive retail equity under all scenarios; positive base top5 advantage in>=2of3 validation folds; >=20 closed retail trades and drawdown>=-25%. No automatic promotion.',
        'policy':asdict(Policy()),'limits':['Previously examined history, no fresh holdout','Current universe/industry and provider-adjusted prices; survivorship remains',
            'Common rows inherited from original tournament prepare(), including its finite legacy-feature restriction; unused historical fundamentals are not model inputs',
            '20-stock sample is small; bootstrap intervals are descriptive; no new paper trial or production promotion']}
    path=args.output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=protocol:raise RuntimeError('Protocol changed; choose a new output directory')
    if (args.output/'report.json').exists():print('Completed frozen experiment; inspect report.json');return
    atomic_json(path,protocol);print('PROTOCOL FROZEN before training',flush=True)
    frame,bars,sessions,statistics,_=prepare(inputs,args.base)
    symbols={r['symbol'] for r in report['protocol']['symbols']}
    features=sector_features(frame,bars,sessions)
    features=features[features.symbol.isin(symbols) & (features.trade_date>='2022-01-01')].reset_index(drop=True)
    features=attach_earnings(features,report['accepted'])
    features.to_parquet(args.output/'features.parquet',index=False)
    ref=pd.concat(refs,ignore_index=True);ref=ref[ref.symbol.isin(symbols)]
    common=features[['symbol','trade_date']].merge(ref,validate='one_to_one')
    events=load_events();predictions={n:[] for n in ARMS};receipts={}
    for f in range(1,6):
        start=folds[f]['start'];dates=sessions[:sessions.index(start)-63]
        train=training_rows(features,'execution',dates,start)
        test=features.merge(common.loc[common.fold==f,['symbol','trade_date']],validate='one_to_one')
        if train.empty or test.empty:raise RuntimeError('Insufficient fixed pilot fold')
        coverage={c:int(train[c].notna().sum()) for c in EARNINGS_FEATURES}
        receipts[str(f)]={'train_rows':len(train),'test_rows':len(test),'last_train_maturity':train.scheduled_exit_date.max(),'test_start':start,'known_financial_training_rows':coverage}
        for name,columns in ARMS.items():
            out=args.output/f'{name}_fold{f}.parquet';receipt=out.with_suffix('.json')
            if out.exists() and receipt.exists():
                if sha(out)!=json.loads(receipt.read_text())['sha256']:raise RuntimeError('Prediction changed')
                p=pd.read_parquet(out)
            else:
                print(f'FIT {name} fold{f}: {len(train)} train / {len(test)} test',flush=True)
                model=InformationModel(columns).fit(train)
                p=test[['symbol','trade_date']].copy();p['fold']=f;p['score']=model.predict(test)
                p.to_parquet(out,index=False);atomic_json(receipt,{'sha256':sha(out),'protocol_sha256':sha(path)})
            predictions[name].append(p)
    scored={n:pd.concat(p,ignore_index=True) for n,p in predictions.items()}
    scored['current_model']=common[['symbol','trade_date','score','fold']].copy()
    scored['momentum']=common[['symbol','trade_date','fold']].copy()
    scored['momentum']['score']=[statistics[(r.symbol,r.trade_date)]['momentum'] for r in scored['momentum'].itertuples()]
    def period(which):
        return evaluate({n:p[p.fold.isin(which)].copy() for n,p in scored.items()},statistics,bars,sessions,events,folds[min(which)]['start'],folds[max(which)]['end'])
    validation=period([1,2,3]);validation_folds=[period([f]) for f in (1,2,3)]
    reasons=selection_reasons(validation,validation_folds)
    selection={'selected':'financial' if not reasons else None,'reasons':reasons,'assessment_used':False,'protocol_sha256':sha(path)}
    atomic_json(args.output/'selection.json',selection);print('VALIDATION selection locked:',selection['selected'],flush=True)
    assessment=period([4,5]);assessment_folds=[period([f]) for f in (4,5)]
    quality={n:{str(f):score_quality(p[p.fold==f][['symbol','trade_date','score']],features,folds[f]['end']) for f in range(1,6)} for n,p in scored.items()}
    result={'status':'historical_research','production_changed':False,'promotion_allowed':False,'protocol':protocol,'selection':selection,
        'validation':validation,'validation_folds':validation_folds,'assessment':assessment,'assessment_folds':assessment_folds,
        'quality':quality,'training_receipts':receipts,'feature_coverage':{c:float(features[c].notna().mean()) for c in EARNINGS_FEATURES},
        'artifacts':{'features.parquet':sha(args.output/'features.parquet'),**{p.name:sha(p) for p in args.output.glob('*_fold*.parquet')}}}
    atomic_json(args.output/'report.json',result)
    print('RESEARCH COMPLETE; production and existing paper trial unchanged',flush=True)


if __name__=='__main__':main()
