"""Append completed quote observations and value frozen paper cohorts only.

No model fitting, rescoring, new prediction capture or production writes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ml.evaluation.forward_tracking import IST,atomic_json


def completed(day,now):
    """Conservative after-close buffer; benchmark quotes define trading dates."""
    if day<now.date().isoformat():return True
    return day==now.date().isoformat() and (now.hour,now.minute)>=(18,0)


def merge_observations(previous,rows):
    """First observed prices stay immutable; later revisions are quarantined."""
    result=dict(previous);revisions=[]
    for row in rows:
        key=row['symbol']+'|'+row['trade_date']
        if key in result:
            if any(abs(row[k]/result[key][k]-1)>1e-8 if result[key].get(k) and row.get(k) else row.get(k)!=result[key].get(k) for k in ('open','low','close')):
                revisions.append({'symbol':row['symbol'],'trade_date':row['trade_date'],'reason':'Observed OHLC basis changed'})
            continue
        result[key]=row
    return result,revisions


def unique_revisions(rows):
    return list({(r['symbol'],r['trade_date'],r['reason']):r for r in rows}.values())


def held_window_revisions(record, basket, sessions, revisions):
    future = [i for i,day in enumerate(sessions) if day > record['publication_date']]
    if not future:
        return []
    start = future[0]
    held_dates = set(sessions[start:start + record['policy']['horizon'] + 1])
    symbols = {r['symbol'] for r in basket}
    return [r for r in unique_revisions(revisions) if r['symbol'] in symbols and r['trade_date'] in held_dates]


def verify_frozen(research):
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    frozen=json.loads((research/'paper_models.json').read_text())
    if digest(research/'protocol.json')!=frozen['protocol_sha256']:
        raise RuntimeError('Frozen trial protocol changed')
    protocol=json.loads((research/'protocol.json').read_text())
    for name,expected in protocol['source_hashes'].items():
        if digest(ROOT/'ml/evaluation'/name)!=expected:
            raise RuntimeError('Frozen trial source changed: '+name)
    for model in frozen['models'].values():
        if digest(research/model['file'])!=model['sha256']:
            raise RuntimeError('Frozen paper model changed')


def main():
    import pandas as pd
    import yfinance as yf
    yf.config.debug.hide_exceptions = False
    from ml.evaluation.paper_portfolio import evaluate_book
    from ml.evaluation.information_research import prospective_summary
    from ml.evaluation.corporate_actions import load_events
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research',type=Path,default=ROOT/'ml/evaluation/results/information_research_20261109')
    parser.add_argument('--seed',type=Path,default=ROOT/'ml/evaluation/results/paper_refresh_20261108')
    args=parser.parse_args();paper=args.research/'paper';now=datetime.now(IST)
    verify_frozen(args.research)
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    files=sorted((paper/'predictions').glob('*.json'))
    if not files:raise RuntimeError('No frozen cohorts to observe')
    records=[json.loads(p.read_text()) for p in files]
    begin=min(r['publication_date'] for r in records)
    symbols=sorted({row['symbol'] for record in records for basket in record['baskets'].values() for row in basket}|{'^NSEI'})
    state_file=paper/'observation_state.json'
    state=json.loads(state_file.read_text()) if state_file.exists() else {'bars':{},'revisions':[]}
    def fetch(symbol):
        try:
            data=yf.Ticker(symbol).history(start=begin,end=(now.date()+timedelta(days=1)).isoformat(),auto_adjust=True,repair=True,timeout=15)
            rows=[{'symbol':symbol,'trade_date':day.date().isoformat(),**{k:float(row[v]) if pd.notna(row[v]) else None for k,v in [('open','Open'),('low','Low'),('close','Close'),('volume','Volume')]}}
                  for day,row in data.iterrows() if completed(day.date().isoformat(),now)]
            return {'symbol':symbol,'status':'complete' if rows else 'no_completed_rows','rows':rows}
        except Exception as e:return {'symbol':symbol,'status':'error','reason':str(e)[:220],'rows':[]}
    with ThreadPoolExecutor(max_workers=3) as executor:results=list(executor.map(fetch,symbols))
    rows=[row for result in results for row in result['rows']]
    observed,revisions=merge_observations(state['bars'],rows)
    run={'observed_at':now.isoformat(),'source':'yfinance auto-adjusted completed OHLC; first observation preserved',
         'algorithm_sha256':digest(Path(__file__)),'results':results,'new_basis_revisions':revisions,'production_changed':False}
    stamp=now.strftime('%Y%m%dT%H%M%S%f')
    atomic_json(paper/'observations'/f'{stamp}.json',run)
    all_revisions=unique_revisions(state['revisions']+revisions)
    atomic_json(state_file,{'bars':observed,'revisions':all_revisions,'last_observed_at':now.isoformat()})
    seed_manifest=json.loads((args.seed/'manifest.json').read_text())
    if digest(args.seed/'bars.parquet')!=seed_manifest['files']['bars.parquet']:raise RuntimeError('Seed quote snapshot changed')
    raw=pd.read_parquet(args.seed/'bars.parquet')
    bars={(r.symbol,r.trade_date):{k:getattr(r,k) if pd.notna(getattr(r,k)) else None for k in ('open','low','close','volume')} for r in raw.itertuples()}
    for row in observed.values():bars[(row['symbol'],row['trade_date'])]={k:row[k] for k in ('open','low','close','volume')}
    sessions=sorted({day for symbol,day in bars if symbol=='^NSEI'})
    events=load_events()
    for file,record in zip(files,records):
        books={}
        for name,basket in record['baskets'].items():
            receipt_path=paper/'entries'/f'{file.stem}_{name}.json'
            receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            relevant=held_window_revisions(record,basket,sessions,all_revisions)
            result=evaluate_book(record,basket,bars,sessions,events,receipt)
            if relevant:
                result={'status':'incomplete','net_return':None,'reason':'Observed held-window price basis changed; sourced reconciliation required','revisions':relevant}
            elif receipt is None and result.get('entry_receipt'):
                # Reuse the established immutable entry writer; no resizes on retry.
                from ml.evaluation.information_research import write_frozen
                write_frozen(receipt_path,result['entry_receipt'])
            books[name]=result
        atomic_json(paper/'outcomes'/file.name,books)
    assessment=prospective_summary(paper,sessions)
    atomic_json(paper/'assessment.json',assessment)
    status_file=paper/'status.json';status=json.loads(status_file.read_text()) if status_file.exists() else {}
    atomic_json(status_file,{**status,**assessment,'last_valued_price_session':sessions[-1],
        'last_observed_at':now.isoformat(),'quote_errors':sum(r['status']=='error' for r in results),
        'price_basis_revisions':len(all_revisions),'production_changed':False})
    print(json.dumps({'symbols_observed':len(symbols),'latest_completed_session':sessions[-1],
        'captured_cohorts':assessment['captured_cohorts'],'matured_cohorts':assessment['matured_cohorts'],
        'quote_errors':sum(r['status']=='error' for r in results)}),flush=True)


if __name__=='__main__':main()
