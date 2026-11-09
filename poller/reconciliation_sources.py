"""Discover timestamped original NSE financial PDFs for every frozen pilot gap."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ml.evaluation.forward_tracking import atomic_json,IST


def digest(raw):return hashlib.sha256(raw).hexdigest()



def download_pdf(url,pdf):
    """Bounded range transfers preserve progress without HTTP/2 stream resets."""
    from curl_cffi import requests,CurlHttpVersion
    partial=pdf.with_suffix('.partial');metadata=pdf.with_suffix('.transfer.json')
    saved=json.loads(metadata.read_text()) if metadata.exists() else None
    if partial.exists() and not saved:partial.unlink()
    offset=partial.stat().st_size if partial.exists() else 0
    total=saved.get('total') if saved else None
    token=saved.get('validator') if saved else None
    while total is None or offset<total:
        headers={'Range':f'bytes={offset}-{offset+1048575}'}
        if token:headers['If-Range']=token
        response=requests.get(url,impersonate='chrome',http_version=CurlHttpVersion.V1_1,headers=headers,timeout=30)
        response.raise_for_status()
        if response.status_code!=206:
            if response.status_code==200 and response.content.startswith(b'%PDF'):
                partial.write_bytes(response.content);total=len(response.content);offset=total;break
            raise ValueError('Original PDF range response unavailable')
        content_range=response.headers.get('Content-Range','')
        import re
        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',content_range)
        if not match or int(match[1])!=offset:raise ValueError('Invalid original PDF range')
        size=int(match[3])
        validator=response.headers.get('ETag') or response.headers.get('Last-Modified')
        if total is not None and (size!=total or token and validator!=token):raise ValueError('PDF version changed during transfer')
        total=size;token=validator
        expected=int(match[2])-offset+1
        if len(response.content)!=expected:raise ValueError('Truncated original PDF range')
        with partial.open('ab') as f:f.write(response.content)
        offset+=len(response.content)
        atomic_json(metadata,{'source':url,'total':total,'validator':token})
    body=partial.read_bytes()
    if len(body)!=total or not body.startswith(b'%PDF') or b'%%EOF' not in body[-2048:]:raise ValueError('Complete original PDF unavailable')
    partial.replace(pdf)


def main():
    from curl_cffi import requests
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'ml/evaluation/results/financial_pilot_v1')
    args=parser.parse_args();base=args.directory;root=base/'reconciliation';root.mkdir(exist_ok=True)
    queue=json.loads((base/'review_queue.json').read_text())['items']
    config=json.loads((ROOT/'config/financial_pilot_v1.json').read_text())
    identities={r['symbol']:r for r in config['symbols']}
    def fetch(item):
        symbol=item['symbol'];end=datetime.fromisoformat(item['period_end'])
        prior=root/'cases'/f"{symbol}_{item['period_end']}.json"
        if prior.exists():
            old=json.loads(prior.read_text())
            if old['status']=='retrieved' and not old.get('errors'):return old
        first=(end+timedelta(days=1)).strftime('%d-%m-%Y');last=(end+timedelta(days=100)).strftime('%d-%m-%Y')
        url='https://www.nseindia.com/api/corporate-announcements?'+urlencode({'index':'equities','symbol':symbol.removesuffix('.NS'),'from_date':first,'to_date':last})
        path=root/'indexes'/f"{symbol}_{item['period_end']}.json";path.parent.mkdir(exist_ok=True)
        try:
            if not path.exists():
                response=requests.get(url,impersonate='chrome',timeout=30);response.raise_for_status()
                records=response.json()
                if not isinstance(records,list):raise ValueError('Announcement schema changed')
                atomic_json(path,{'source':url,'observed_at':datetime.now(IST).isoformat(),'records':records})
            data=json.loads(path.read_text());records=data['records']
            candidates=[]
            for row in records:
                subject=(row.get('desc') or '').lower()
                attachment=row.get('attchmntFile') or ''
                if not ('financial result' in subject or 'outcome of board' in subject):continue
                if not attachment.startswith('https://nsearchives.nseindia.com/corporate/') or not attachment.lower().endswith('.pdf'):continue
                identity_status='exact_isin' if row.get('sm_isin')==identities[symbol]['isin'] else 'requires_issuer_review'
                if identity_status!='exact_isin' and (row.get('symbol')!=symbol.removesuffix('.NS') or not row.get('sm_name')):continue
                pub=max(datetime.strptime(row[k],'%d-%b-%Y %H:%M:%S').replace(tzinfo=IST) for k in ('an_dt','exchdisstime'))
                candidates.append({'symbol':symbol,'period_end':item['period_end'],'source':attachment,'published_at':pub.isoformat(),
                    'index_source':data['source'],'index_file':str(path.resolve()),'index_sha256':digest(path.read_bytes()),'isin':row['sm_isin'],'expected_isin':identities[symbol]['isin'],
                    'issuer_name':row.get('sm_name'),'exchange_symbol':row.get('symbol'),'identity_status':identity_status,
                    'subject':row['desc'],'description':row.get('attchmntText'),'exchange_id':row.get('seq_id')})
            # Retain original releases, not only the last amendment. Earliest four
            # matching official attachments bound the download work per quarter.
            candidates=sorted(candidates,key=lambda r:('financial result' not in r['subject'].lower(),r['published_at']))[:4]
            valid=[]
            errors=[]
            for row in candidates:
                try:
                    sha=digest(row['source'].encode());pdf=root/'pdf'/f'{sha}.pdf';pdf.parent.mkdir(exist_ok=True)
                    txt=pdf.with_suffix('.txt')
                    if not pdf.exists():
                        download_pdf(row['source'],pdf)
                    if not txt.exists():subprocess.run(['pdftotext','-layout',str(pdf),str(txt)],check=True,capture_output=True,timeout=30)
                    row.update(pdf_file=str(pdf.resolve()),pdf_sha256=digest(pdf.read_bytes()),text_file=str(txt.resolve()),text_sha256=digest(txt.read_bytes()),first_seen_at=datetime.now(IST).isoformat())
                    valid.append(row)
                except Exception as e:
                    errors.append({'source':row['source'],'reason':str(e)[:200]})
            return {'symbol':symbol,'period_end':item['period_end'],'candidates':valid,'errors':errors,'status':'retrieved' if valid else 'no_retrieved_release'}
        except Exception as e:return {'symbol':symbol,'period_end':item['period_end'],'candidates':[],'status':'error','reason':str(e)[:250]}
    results=[]
    with ThreadPoolExecutor(max_workers=3) as executor:
        pending=[executor.submit(fetch,item) for item in queue]
        for i,future in enumerate(as_completed(pending),1):
            value=future.result()
            atomic_json(root/'cases'/f"{value['symbol']}_{value['period_end']}.json",value)
            results.append(value);print(f"RECONCILIATION {i}/{len(queue)} {value['symbol']} {value['period_end']} {value['status']} ({len(value['candidates'])}) {value.get('reason',value.get('errors',[]))}",flush=True)
    atomic_json(root/'discovery.json',{'observed_at':datetime.now(IST).isoformat(),'pilot_config_sha256':digest((ROOT/'config/financial_pilot_v1.json').read_bytes()),'results':results})


if __name__=='__main__':main()
