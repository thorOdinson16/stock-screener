"""Execution-net and leave-one-out sector targets for bounded research."""
import numpy as np
import pandas as pd
from .forecast_outcomes import outcome


def sector_forward_returns(bar_frame,industries,sessions,horizon,events,symbols,min_peers=5,min_fraction=.95,min_history=63):
    """Next-close to scheduled-exit gross passive sector benchmark.

    Today's universe/industry taxonomy is an explicit historical limitation.
    Missing/restructuring peer outcomes are unknown, never zero or forward-filled.
    The peer denominator uses past-only tradability within the supplied universe,
    independent of future outcomes.
    """
    names=sorted(industries)
    prices=bar_frame[bar_frame.symbol.isin(names)].pivot(index='trade_date',columns='symbol',values='close').reindex(index=sessions,columns=names)
    prices=prices.where(prices>0)
    future=prices.shift(-(horizon+1))/prices.shift(-1)-1
    full=prices.rolling(horizon+1,min_periods=horizon+1).count().shift(-(horizon+1))==horizon+1
    eligible=prices.rolling(min_history,min_periods=min_history).count()==min_history
    future=future.where(full & eligible)
    dates=pd.Series(sessions,index=sessions);entry=dates.shift(-1);exit_date=dates.shift(-(horizon+1))
    for event in events:
        affected=sorted(set(event['symbols']) & set(names))
        if not affected:continue
        mask=(entry<=event['impact_end']) & (exit_date>=event['impact_start'])
        future.loc[mask,affected]=np.nan
    groups={}
    for symbol,industry in industries.items():groups.setdefault(industry,[]).append(symbol)
    rows=[]
    for industry,members in groups.items():
        part=future[members];total=part.sum(axis=1);count=part.count(axis=1)
        for symbol in sorted(set(members)&set(symbols)):
            peers=eligible[members].sum(axis=1)-eligible[symbol].astype(int)
            known=count-part[symbol].notna().astype(int)
            fraction=known/peers.where(peers>0)
            mean=(total-part[symbol].fillna(0))/known.where(known>0)
            valid=(known>=min_peers)&(fraction>=min_fraction)&(industry!='Unknown')
            rows.append(pd.DataFrame({'symbol':symbol,'trade_date':sessions,'sector_forward':mean.where(valid).to_numpy(),
                'sector_known_peers':known.to_numpy(),'sector_expected_peers':peers.to_numpy(),'sector_coverage':fraction.to_numpy()}))
    return pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=['symbol','trade_date','sector_forward','sector_known_peers','sector_expected_peers','sector_coverage'])


def execution_targets(frame,bars,sessions,events,policy):
    positions={d:i for i,d in enumerate(sessions)};rows=[]
    for r in frame[['symbol','trade_date']].itertuples(index=False):
        position=positions.get(r.trade_date)
        value=outcome(r.symbol,position+1,policy.horizon,bars,sessions,events,policy) if position is not None else {'status':'missing_calendar'}
        rows.append({'symbol':r.symbol,'trade_date':r.trade_date,'execution_net':value.get('net_return'),
            'target_status':value['status'],'target_maturity':value.get('scheduled_exit_date')})
    return pd.DataFrame(rows)


def matured_training(frame,train_dates,before):
    mask=frame.trade_date.isin(train_dates)&frame.execution_net.notna()&frame.sector_forward.notna()&frame.target_maturity.notna()&(frame.target_maturity<before)
    result=frame.loc[mask].sort_values(['trade_date','symbol']).copy()
    result['sector_relative_net']=result.execution_net-result.sector_forward
    return result
