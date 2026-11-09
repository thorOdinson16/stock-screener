"""Archive public provider documentation; never substitute it for estimate data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ml.evaluation.forward_tracking import atomic_json,IST

SOURCES=[
 ('capital_iq','https://www.spglobal.com/market-intelligence/en/solutions/capital-iq-estimates','Documents point-in-time snapshots and pre-report surprise comparisons; no licensed stock records retrieved'),
 ('ibes','https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/estimates-API','Documents estimates delivery/history; access and pilot-security coverage unverified'),
 ('infosys_guidance','https://www.infosys.com/investors/reports-filings/financials/guidance-vs-actuals-usd.html','Current summary mixes guidance and subsequent actuals; not a versioned quarter-by-quarter revision archive'),
 ('infosys_call','https://www.infosys.com/investors/reports-filings/quarterly-results/2022-2023/q3/documents/transcripts/press-conference.pdf','Dated call contains guidance changes; call date alone does not verify transcript upload availability')]


def main():
    from curl_cffi import requests
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'ml/evaluation/results/decision_experiment_20261109_v3/source_audit')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    def fetch(spec):
        name,url,note=spec;file=args.output/(name+('.pdf' if url.endswith('.pdf') else '.html'))
        try:
            r=requests.get(url,impersonate='chrome',timeout=25);r.raise_for_status();file.write_bytes(r.content)
            return {'name':name,'source':url,'observed_at':datetime.now(IST).isoformat(),'file':str(file.resolve()),'sha256':hashlib.sha256(r.content).hexdigest(),'status':'retrieved','note':note}
        except Exception as e:return {'name':name,'source':url,'status':'fetch_error','reason':str(e)[:200],'note':note}
    with ThreadPoolExecutor(max_workers=3) as executor:receipts=list(executor.map(fetch,SOURCES))
    ledger=json.loads((ROOT/'config/pilot_reconciliation_reviews_v1.json').read_text())
    example=next(r for r in ledger['reviews'] if r['symbol']=='INFY.NS' and r['period_end']=='2022-12-31')
    evidence=example['evidence'];text=ROOT/evidence['text_file']
    pages=text.read_text().split('\f')
    mentions=[i for i,p in enumerate(pages,1) if 'Revenue guidance for FY23 revised to 16.0%-16.5%' in p]
    audit={'status':'insufficient_verified_consensus_history','consensus_records':0,'estimate_revision_records':0,
        'scope':'No usable analyst-consensus or estimate-revision archive supplied in this repository; not a claim that such data is unavailable from providers',
        'surprise_backtest_run':False,'provider_access_or_pilot_coverage_verified':False,'sources':receipts,
        'guidance_example':{'symbol':'INFY.NS','period_end':'2022-12-31','source':evidence['source'],'document_sha256':evidence['pdf_sha256'],
            'publication':evidence['published_at'],'pages':mentions,'note':'Original exchange attachment contains a revised FY23 guidance range. One case does not establish comparable multi-issuer historical coverage; excluded from predictive inputs'},
        'required_estimate_fields':['canonical security/ISIN','metric and scope, including adjusted vs statutory and owners vs group',
            'currency and units','fiscal period end','consensus value and contributor count','historical snapshot effective timestamp',
            'immutable original-source receipt and publication timestamp strictly before original earnings announcement',
            'actual original announcement timestamp, value and same accounting definition'],
        'policy':'No current estimates backdated, no future actuals used as expectations, no broker forecast relabelled consensus, no guidance inserted for issuers without evidence',
        'production_changed':False}
    atomic_json(args.output/'report.json',audit)
    print(json.dumps({'status':audit['status'],'consensus_records':0,'guidance_example_pages':mentions,'source_fetch_errors':sum(r['status']!='retrieved' for r in receipts)}),flush=True)


if __name__=='__main__':main()
