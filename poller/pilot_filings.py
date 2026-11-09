"""Collect all pages of both NSE filing systems for a pre-frozen pilot."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ml.evaluation.forward_tracking import atomic_json,IST
if __package__:
    from .research_information import exclusive
else:
    from research_information import exclusive


def digest(raw):return hashlib.sha256(raw).hexdigest()


def normalize(row,source,symbol,integrated=False):
    if integrated:
        if row.get('type')!='Integrated Filing- Financials' or row.get('consolidated')!='Consolidated':return None
        # creation_Date is the dissemination/record creation field exposed by
        # NSE's live table. Use max of received, created and revised timestamps.
        times=[row.get(k) for k in ('broadcast_Date','creation_Date','revised_Date') if row.get(k)]
        if not row.get('creation_Date') or not row.get('broadcast_Date'):raise ValueError('Missing exchange timestamps')
        end=datetime.strptime(row['qe_Date'],'%d-%b-%Y').strftime('%d-%b-%Y')
    else:
        if row.get('consolidated')!='Consolidated':return None
        if row.get('cumulative')!='Non-cumulative' and row.get('period')!='Annual':return None
        if not row.get('broadCastDate') or not row.get('exchdisstime'):raise ValueError('Missing exchange timestamps')
        times=[row['broadCastDate'],row['exchdisstime']]
        end=row['toDate']
    url=row.get('xbrl') or ''
    if not url.startswith('https://nsearchives.nseindia.com/corporate/') or not url.endswith('.xml'):raise ValueError('Missing original XBRL link')
    return {'symbol':symbol,'period_end':end,'published_times':times,'source':url,'index_source':source,
            'exchange_symbol':row.get('symbol'),'index_isin':row.get('isin'),'filing_kind':row.get('type_Sub','legacy'),'raw_record_sha256':digest(json.dumps(row,sort_keys=True).encode())}


def main():
    from curl_cffi import requests
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/financial_pilot_v1.json')
    parser.add_argument('--output',type=Path,default=ROOT/'ml/evaluation/results/financial_pilot_v1')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    root=args.output;root.mkdir(parents=True,exist_ok=True)
    protocol=json.loads(args.config.read_text());config_hash=digest(args.config.read_bytes())
    if (root/'collection.json').exists() and not args.refresh and not args.resume:
        old=json.loads((root/'collection.json').read_text())
        if old['pilot_config_sha256']!=config_hash:raise RuntimeError('Pilot selection changed')
        print('Collection checkpoint exists; use --refresh to append observations')
        return
    now=datetime.now(IST)
    receipts,errors,queue,index_rejected=[],[],[],[]
    session=requests.Session(impersonate='chrome')
    # Cached raw responses make interrupted retries resumable. Refresh requests
    # append new source bytes/receipts and never rewrite old filing observations.
    def get_index(url):
        cache=root/'requests'/digest(url.encode())
        cache.parent.mkdir(exist_ok=True)
        if cache.exists() and not args.refresh:
            raw=cache.read_bytes()
        else:
            response=session.get(url,timeout=30);response.raise_for_status();raw=response.content
            json.loads(raw)
            cache.write_bytes(raw)
        sha=digest(raw);path=root/'raw'/sha;path.parent.mkdir(exist_ok=True)
        if not path.exists():path.write_bytes(raw)
        receipts.append({'source':url,'sha256':sha,'bytes':len(raw),'observed_at':datetime.now(IST).isoformat()})
        return json.loads(raw)
    for spec in protocol['symbols']:
        symbol=spec['symbol'];nse=symbol.removesuffix('.NS')
        dates={'symbol':nse,'from_date':datetime.fromisoformat(protocol['history_start']).strftime('%d-%m-%Y'),'to_date':now.strftime('%d-%m-%Y')}
        for form in ('Quarterly','Annual'):
            legacy='https://www.nseindia.com/api/corporates-financial-results?'+urlencode({'index':'equities','period':form,**dates})
            try:
                data=get_index(legacy)
                if not isinstance(data,list):raise ValueError('Legacy schema changed')
                for row in data:
                    try:
                        record=normalize(row,legacy,symbol)
                        if record:queue.append(record)
                    except (KeyError,ValueError) as e:index_rejected.append({'symbol':symbol,'source':legacy,'reason':str(e),'record':row})
            except Exception as e:errors.append({'symbol':symbol,'source':legacy,'reason':str(e)[:200]})
        first_total=None;seen_rows=0;page_ids=set()
        try:
            page=1
            while True:
                url='https://www.nseindia.com/api/integrated-filing-results?'+urlencode({'index':'equities','type':'Integrated Filing- Financials',**dates,'page':page,'size':100})
                data=get_index(url)
                if not isinstance(data,dict) or not isinstance(data.get('data'),list) or 'totalCount' not in data:raise ValueError('Integrated pagination schema changed')
                total=int(data['totalCount'])
                if first_total is None:first_total=total
                if first_total!=total:raise ValueError('Pagination total changed during collection; retry bounded window')
                rows=data['data'];seen_rows+=len(rows)
                ids=[str(r.get('seq_Id') or digest(json.dumps(r,sort_keys=True).encode())) for r in rows]
                if page_ids.intersection(ids) or len(ids)!=len(set(ids)):raise ValueError('Repeated integrated page/record; pagination is incomplete')
                page_ids.update(ids)
                for row in rows:
                    try:
                        record=normalize(row,url,symbol,True)
                        if record:queue.append(record)
                    except (KeyError,ValueError) as e:index_rejected.append({'symbol':symbol,'source':url,'reason':str(e),'record':row})
                if seen_rows>=total:break
                if not rows:raise ValueError('Pagination ended before totalCount')
                page+=1
                if page>100:raise ValueError('Unexpected pagination size; refusing truncation')
        except Exception as e:errors.append({'symbol':symbol,'source':'integrated','reason':str(e)[:200]})
        print(f'PILOT indexes {symbol}: {len(queue)} candidate filings',flush=True)
    # Keep all original/revision URLs. Repeated URL with conflicting publication
    # evidence is quarantined, not assigned the earliest time to improve coverage.
    versions={}
    for row in queue:versions.setdefault(row['source'],[]).append(row)
    selected=[]
    for url,rows in versions.items():
        # The same original can be indexed as both quarterly and annual. Use
        # the latest evidenced dissemination, never the earliest convenient time.
        times=sorted(set(t for r in rows for t in r['published_times']))
        selected.append({**rows[0],'published_times':times,'index_sources':sorted(set(r['index_source'] for r in rows))})
    def download(row):
        key=digest(row['source'].encode());cache=root/'documents'/key;cache.parent.mkdir(exist_ok=True)
        try:
            if not cache.exists() or args.refresh:
                response=requests.get(row['source'],impersonate='chrome',timeout=20);response.raise_for_status()
                raw=response.content
                if b'xbrl' not in raw[:4000].lower():raise ValueError('Original XML unavailable')
                cache.write_bytes(raw)
            raw=cache.read_bytes();sha=digest(raw);body=root/'raw'/sha
            if not body.exists():body.write_bytes(raw)
            record={**row,'document_file':str(body.resolve()),'document_sha256':sha,'first_seen_at':datetime.now(IST).isoformat()}
            identity=digest(json.dumps([row['source'],sha,row['published_times']],sort_keys=True).encode())
            receipt=root/'filings'/f'{identity}.json';exclusive(receipt,record)
            old=json.loads(receipt.read_text())
            return {**old,**row,'identity_evidence_observed_at':datetime.now(IST).isoformat()}
        except Exception as e:return {'error':True,'symbol':row['symbol'],'source':row['source'],'reason':str(e)[:200]}
    materialized=[]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i,result in enumerate(executor.map(download,selected),1):
            if result.get('error'):errors.append(result)
            else:materialized.append(result)
            if i%50==0:print(f'PILOT documents {i}/{len(selected)}',flush=True)
    observations=[json.loads(p.read_text()) for p in sorted((root/'filings').glob('*.json'))]
    view={}
    for row in observations+materialized:
        key=(row['source'],row['document_sha256'])
        previous=view.get(key)
        if previous:
            times=sorted(set(previous['published_times']+row['published_times']))
            row={**previous,**row,'published_times':times,'first_seen_at':min(previous['first_seen_at'],row['first_seen_at'])}
        view[key]=row
    documents=list(view.values())
    result={'pilot_config_sha256':config_hash,'observed_at':datetime.now(IST).isoformat(),'documents':documents,'index_receipts':receipts,
            'errors':errors,'rejected_index_records':index_rejected,'pagination_complete':not errors,'selected_documents':len(selected),
            'price_snapshot':str((ROOT/'ml/evaluation/results/paper_refresh_20261108').resolve()),'production_changed':False}
    stamp=datetime.now(IST).strftime('%Y%m%dT%H%M%S%f')
    exclusive(root/'collections'/f'{stamp}.json',result)
    atomic_json(root/'collection.json',result)
    print(json.dumps({'documents':len(documents),'errors':len(errors),'pagination_complete':not errors}),flush=True)


if __name__=='__main__':main()
