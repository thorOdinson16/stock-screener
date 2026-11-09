"""Fixed liquid-stock financial pilot and point-in-time data readiness gates."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import sha
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.information import document_facts, earnings_at, EARNINGS_FEATURES

CONFIG = ROOT / 'config/financial_pilot_v1.json'
DEFAULT = ROOT / 'ml/evaluation/results/financial_pilot_v1'


def choose_pilot(bars, universe, selection_date='2023-06-30', count=20, industry_cap=3):
    calendar = sorted(bars.loc[(bars.symbol=='^NSEI') & (bars.trade_date<=selection_date),'trade_date'].unique())
    days = calendar[-63:]
    rows = []
    for spec in universe:
        if spec['industry'] in ('Financial Services', 'Unknown'):
            continue  # One comparable operating-company taxonomy for the pilot.
        part=bars[(bars.symbol==spec['yf_symbol']) & bars.trade_date.isin(days)]
        if len(part)!=63 or not np.isfinite(part[['close','volume']].to_numpy()).all() or (part[['close','volume']]<=0).any().any():
            continue
        rows.append({'symbol':spec['yf_symbol'],'industry':spec['industry'],'isin':spec['isin'],
                     'selection_median_turnover':float((part.close*part.volume).median())})
    selected, sectors=[],{}
    for row in sorted(rows,key=lambda r:(-r['selection_median_turnover'],r['symbol'])):
        if sectors.get(row['industry'],0)>=industry_cap:continue
        selected.append(row);sectors[row['industry']]=sectors.get(row['industry'],0)+1
        if len(selected)==count:break
    if len(selected)!=count:raise ValueError('Not enough eligible liquid stocks')
    return selected


def initialize(config=CONFIG, snapshot=None):
    if config.exists():
        print('Pilot selection already frozen; not reselecting')
        return
    snapshot=Path(snapshot or ROOT/'ml/evaluation/results/paper_refresh_20261108')
    bars=pd.read_parquet(snapshot/'bars.parquet')
    universe_file=ROOT/'poller/universe/universe_cache.json'
    universe=json.loads(universe_file.read_text())['stocks']
    protocol={'version':1,'created_at':datetime.now(IST).isoformat(),
        'selection_date':'2023-06-30','selection':'Top median close*volume over 63 completed benchmark sessions, max3 per industry; exclude Financial Services and Unknown; no return outcomes used',
        'selection_limits':'Current universe/industry membership and provider-adjusted historical prices; survivorship and hindsight membership remain',
        'source_manifest_sha256':sha(snapshot/'manifest.json'),'bars_sha256':sha(snapshot/'bars.parquet'),
        'universe_sha256':sha(universe_file),'symbols':choose_pilot(bars,universe),
        'history_start':'2022-01-01','audit_start':'2024-01-01','audit_end':'2026-10-26',
        'required_quarters_start':'2022Q1','required_quarters_end':'2026Q2',
        'thresholds':{'minimum_signal_dates':504,'margin_fraction':.95,'qoq_fraction':.95,'yoy_fraction':.90,
                      'required_date_pass_fraction':.95,'minimum_quarter_pass_fraction':.90,'required_quarter_cell_fraction':.95,
                      'latest_date_must_pass':True},
        'availability':'Verified publication+next calendar day; require a prior quarter and year public by each signal date; maximum filing age180 days',
        'readiness_scope':'Data coverage only; not permission to train, evidence of alpha, production promotion or removal of corporate-action limitations'}
    config.parent.mkdir(exist_ok=True)
    with config.open('x') as f:json.dump(protocol,f,indent=2)
    print('FROZEN PILOT:',', '.join(r['symbol'] for r in protocol['symbols']),flush=True)


def readiness(daily, quarter_cells, thresholds):
    reasons=[]
    if len(daily)<thresholds['minimum_signal_dates']:reasons.append('Insufficient signal dates')
    fraction=float(daily.ready.mean()) if len(daily) else 0.
    if fraction<thresholds['required_date_pass_fraction']:reasons.append('Too few signal dates meet financial coverage thresholds')
    quarters=daily.assign(quarter=pd.to_datetime(daily.trade_date).dt.to_period('Q').astype(str)).groupby('quarter').ready.mean().to_dict() if len(daily) else {}
    if any(v<thresholds['minimum_quarter_pass_fraction'] for v in quarters.values()):reasons.append('At least one signal quarter has inadequate coverage')
    if thresholds['latest_date_must_pass'] and (daily.empty or not bool(daily.iloc[-1].ready)):reasons.append('Last signal date fails coverage')
    cells=float(np.mean([c['accepted'] for c in quarter_cells])) if quarter_cells else 0.
    if cells<thresholds['required_quarter_cell_fraction']:reasons.append('Missing required stock-quarter documents')
    return {'ready':not reasons,'reasons':reasons,'signal_dates':len(daily),'date_pass_fraction':fraction,
            'quarter_pass_fraction':{k:float(v) for k,v in quarters.items()},'quarter_cell_fraction':cells,
            'latest':daily.iloc[-1].to_dict() if len(daily) else None,'production_ready':False}



def parse_pilot_record(row,file,identity):
    """Resolve an old symbol only through an exact exchange-index ISIN binding."""
    import xml.etree.ElementTree as ET
    expected=identity[row['symbol']]['isin']
    original=row
    alias=None
    if row.get('index_isin')==expected and row.get('exchange_symbol')==row['symbol'].removesuffix('.NS'):
        root=ET.fromstring(Path(file).read_bytes())
        names={e.text.strip() for e in root if e.tag.split('}')[-1]=='Symbol' and e.text}
        if len(names)==1:
            as_filed=next(iter(names))+'.NS'
            if as_filed!=row['symbol']:
                alias=as_filed
                row={**row,'symbol':as_filed}
    facts=document_facts(row,file,row['first_seen_at'])
    if alias:
        facts.update(symbol=original['symbol'],as_filed_symbol=alias,
                     identity_basis='Original XML URL bound to frozen security ISIN by official exchange index',index_isin=expected)
    return facts


def audit(directory=DEFAULT, config=CONFIG):
    directory,config=Path(directory),Path(config)
    protocol=json.loads(config.read_text())
    index=directory/'collection.json'
    collection=json.loads(index.read_text())
    if collection['pilot_config_sha256']!=sha(config):raise RuntimeError('Pilot changed; version the dataset')
    accepted,rejected=[],[]
    identity={r['symbol']:r for r in protocol['symbols']}
    for row in collection['documents']:
        try:
            file=Path(row['document_file'])
            if sha(file)!=row['document_sha256']:raise ValueError('Document hash changed')
            value=parse_pilot_record(row,file,identity)
            accepted.append(value)
        except Exception as e:rejected.append({'symbol':row.get('symbol'),'source':row['source'],'reason':str(e)[:200]})
    from ml.evaluation.financial_reconciliation import load_reviews
    reconciled, reconciliation_files, reconciliation_log = load_reviews(directory,config,collection)
    accepted.extend(reconciled)
    # Preserve revisions. If bytes changed under an unchanged URL/timestamp,
    # availability of that version starts after observation, never in the past.
    groups={}
    for row in accepted:groups.setdefault((row['source'],row['published_at']),[]).append(row)
    for versions in groups.values():
        hashes=set()
        for row in sorted(versions,key=lambda r:r['first_seen_at']):
            if hashes and row['document_sha256'] not in hashes:
                day=(pd.Timestamp(row['first_seen_at']).date()+pd.Timedelta(days=1)).isoformat()
                row['available_date']=max(day,row['available_date'])
            hashes.add(row['document_sha256'])
    unique={(r['symbol'],r['document_sha256'],r['published_at']):r for r in accepted}
    accepted=list(unique.values())
    if sha(Path(collection['price_snapshot'])/'bars.parquet')!=protocol['bars_sha256']:
        raise RuntimeError('Frozen pilot price/calendar snapshot changed')
    bars=pd.read_parquet(Path(collection['price_snapshot'])/'bars.parquet',columns=['symbol','trade_date'])
    days=sorted(bars.loc[(bars.symbol=='^NSEI') & bars.trade_date.between(protocol['audit_start'],protocol['audit_end']),'trade_date'].unique())
    symbols=[r['symbol'] for r in protocol['symbols']]
    grouped={s:[r for r in accepted if r['symbol']==s] for s in symbols}
    rows,daily=[],[]
    t=protocol['thresholds']
    for day in days:
        values=[]
        for symbol in symbols:
            value=earnings_at(grouped[symbol],day)
            # No imputation in a coverage gate. Missing stays missing.
            evidence=sorted((r for r in grouped[symbol] if r['available_date']<=day),key=lambda r:(r['period_end'],r['published_at'],r['document_sha256']))
            current=evidence[-1] if evidence and np.isfinite(value['info_margin']) else None
            periods={r['period_end']:r for r in evidence}
            quarter=pd.Period(current['period_end'],freq='Q') if current else None
            previous=periods.get((quarter-1).end_time.date().isoformat()) if quarter else None
            year=periods.get((quarter-4).end_time.date().isoformat()) if quarter else None
            rows.append({'symbol':symbol,'trade_date':day,**{k:float(v) if np.isfinite(v) else None for k,v in value.items()},
                         'filing_sha256':current['document_sha256'] if current else None,
                         'filing_published_at':current['published_at'] if current else None,
                         'qoq_comparison_sha256':previous['document_sha256'] if previous and np.isfinite(value['info_revenue_qoq']) else None,
                         'yoy_comparison_sha256':year['document_sha256'] if year and np.isfinite(value['info_revenue_yoy']) else None})
            values.append(value)
        margin=sum(np.isfinite(v['info_margin']) for v in values)/len(symbols)
        qoq=sum(all(np.isfinite(v[k]) for k in ('info_revenue_qoq','info_profit_change_on_revenue','info_margin_change')) for v in values)/len(symbols)
        yoy=sum(np.isfinite(v['info_revenue_yoy']) for v in values)/len(symbols)
        daily.append({'trade_date':day,'margin_fraction':margin,'qoq_fraction':qoq,'yoy_fraction':yoy,
                      'ready':margin>=t['margin_fraction'] and qoq>=t['qoq_fraction'] and yoy>=t['yoy_fraction']})
    daily=pd.DataFrame(daily)
    features=pd.DataFrame(rows)
    features.to_parquet(directory/'pilot_features.parquet',index=False)
    daily.to_csv(directory/'daily_coverage.csv',index=False)
    quarters=pd.period_range(protocol['required_quarters_start'],protocol['required_quarters_end'],freq='Q')
    cells=[]
    for symbol in symbols:
        for q in quarters:
            end=q.end_time.date().isoformat()
            matches=[r for r in grouped[symbol] if r['period_end']==end]
            cells.append({'symbol':symbol,'period_end':end,'accepted':bool(matches),'versions':len(matches),
                          'first_publication':min((r['published_at'] for r in matches),default=None)})
    review=[]
    rejected_by_url={r['source']:r['reason'] for r in rejected}
    for cell in cells:
        if cell['accepted']:continue
        candidates=[]
        for row in collection['documents']:
            if row['symbol']!=cell['symbol']:continue
            if datetime.strptime(row['period_end'],'%d-%b-%Y').date().isoformat()!=cell['period_end']:continue
            candidates.append({'source':row['source'],'document_sha256':row['document_sha256'],
                               'document_file':row['document_file'],'reason':rejected_by_url.get(row['source'],'No accepted quarter context')})
        review.append({**cell,'diagnosis':'rejected_original_document' if candidates else 'no_verified_original_document',
                       'candidate_documents':candidates,'action':'Reconcile against a timestamped original exchange/issuer document; no inferred or filled values'})
    atomic_json(directory/'review_queue.json',{'items':review,'pilot_config_sha256':sha(config),'production_changed':False})
    gate=readiness(daily,cells,t)
    if collection['errors'] or not collection.get('pagination_complete',False):
        gate['ready']=False;gate['reasons'].append('Source collection errors or incomplete pagination')
    per_symbol={s:{'accepted_filings':len(grouped[s]),'covered_quarters':len({r['period_end'] for r in grouped[s]}),
                   'qoq_date_fraction':float(features[features.symbol==s].info_revenue_qoq.notna().mean()),
                   'yoy_date_fraction':float(features[features.symbol==s].info_revenue_yoy.notna().mean())} for s in symbols}
    report={'status':'ready_for_controlled_research' if gate['ready'] else 'data_not_ready', 'production_changed':False,
        'pilot_config_sha256':sha(config),'protocol':protocol,'readiness':gate,'accepted_filings':len(accepted),'rejected_filings':len(rejected),'reconciled_quarters':len(reconciled),'reconciliation':reconciliation_log,
        'per_symbol':per_symbol,'missing_quarters':[c for c in cells if not c['accepted']],'quarter_cells':cells,
        'source_errors':collection['errors'],'rejected_index_records':collection.get('rejected_index_records',[]),'rejected':rejected,'accepted':accepted,
        'artifacts':{'pilot_features.parquet':sha(directory/'pilot_features.parquet'),'daily_coverage.csv':sha(directory/'daily_coverage.csv'),'review_queue.json':sha(directory/'review_queue.json')},
        'provenance':{'calendar_bars_sha256':protocol['bars_sha256'],'collection_sha256':sha(index),'reconciliation_files':reconciliation_files,'source_hashes':{n:sha(ROOT/'ml/evaluation'/n) for n in ('pilot_coverage.py','information.py','financial_reconciliation.py','reconcile_pilot.py')}},
        'limits':['Publication-safe historical reconstruction is not a contemporaneously observed archive or fresh paper evidence.',
                  'Current universe and industry membership, adjusted liquidity selection and survivorship remain.',
                  'Readiness is coverage, not proof of trading usefulness; no new model trained in this milestone.']}
    atomic_json(directory/'coverage_report.json',report)
    summary=['# Fixed financial pilot coverage','',f"Status: {report['status']}",f"Accepted: {len(accepted)}; rejected: {len(rejected)}; stocks: {len(symbols)}",f"Signal dates meeting gates: {gate['date_pass_fraction']:.1%}",f"Required stock-quarter cells present: {gate['quarter_cell_fraction']:.1%}",'']
    summary += ['- '+reason for reason in gate['reasons']]
    summary += ['','| Stock | Filings | Distinct quarters | QoQ date coverage | YoY date coverage |','|---|---:|---:|---:|---:|']
    for symbol,value in per_symbol.items():summary.append(f"| {symbol} | {value['accepted_filings']} | {value['covered_quarters']} | {value['qoq_date_fraction']:.1%} | {value['yoy_date_fraction']:.1%} |")
    (directory/'coverage_summary.md').write_text('\n'.join(summary)+'\n')
    print(json.dumps({'status':report['status'],'accepted':len(accepted),'rejected':len(rejected),'readiness':gate}),flush=True)
    return report


def require_ready(directory=DEFAULT):
    """Mandatory entry gate for future verified pilot training commands."""
    directory=Path(directory)
    report=json.loads((directory/'coverage_report.json').read_text())
    if not report['readiness']['ready']:raise RuntimeError('Financial data not ready: '+'; '.join(report['readiness']['reasons']))
    if sha(CONFIG)!=report['pilot_config_sha256']:raise RuntimeError('Pilot selection changed')
    for name,expected in report['artifacts'].items():
        if sha(directory/name)!=expected:raise RuntimeError('Pilot data changed; rerun audit')
    for name,expected in report['provenance']['source_hashes'].items():
        if sha(ROOT/'ml/evaluation'/name)!=expected:raise RuntimeError('Coverage definitions changed; rerun audit')
    if sha(directory/'collection.json')!=report['provenance']['collection_sha256']:raise RuntimeError('Collection changed; rerun audit')
    for file,expected in report['provenance'].get('reconciliation_files',{}).items():
        if sha(file)!=expected:raise RuntimeError('Reviewed PDF evidence changed; rerun audit')
    collection=json.loads((directory/'collection.json').read_text())
    for record in collection['documents']:
        if sha(Path(record['document_file']))!=record['document_sha256']:raise RuntimeError('Original filing evidence changed; rerun audit')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init',action='store_true')
    parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--check-ready',action='store_true')
    args=parser.parse_args()
    if args.init:initialize()
    elif args.check_ready:require_ready(args.directory);print('Coverage gate passed; no training performed')
    else:audit(args.directory)
