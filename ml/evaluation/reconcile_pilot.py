"""Corroborate rejected XML quarter facts against timestamped original PDFs.

This never inserts synthetic XML contexts. Unambiguous table evidence is required.
"""
from datetime import datetime,timedelta
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from ml.evaluation.forward_tracking import atomic_json,IST
from ml.evaluation.model_tournament import sha
from ml.evaluation.information import NS,timestamp

BASE=ROOT/'ml/evaluation/results/financial_pilot_v1'
NUM=re.compile(r'(?<![A-Za-z0-9])\(?[−-]?\s*\d[\d,.]*(?:\s*\))?')


def number(token):
    s=re.sub(r'\s','',token).replace('−','-');negative=s.startswith('(') or s.startswith('-')
    s=s.strip('()').lstrip('-')
    if '.' not in s and ',' in s and len(s.rsplit(',',1)[-1])==2:
        left,decimal=s.rsplit(',',1);s=left.replace(',','')+'.'+decimal
    else:s=s.replace(',','')
    if s.count('.')>1:
        parts=s.split('.')
        if any(len(x)!=3 for x in parts[1:-1]) or len(parts[-1])!=2:raise ValueError('Ambiguous number')
        s=''.join(parts[:-1])+'.'+parts[-1]
    result=float(s)
    return -result if negative else result


def tentative_xml(collection,symbol,end):
    values=[]
    for row in collection['documents']:
        if row['symbol']!=symbol:continue
        root=ET.fromstring(Path(row['document_file']).read_bytes());facts={};conflict=False
        for e in root:
            if e.get('contextRef'):
                fs=facts.setdefault(e.get('contextRef'),{})
                name=e.tag.split('}')[-1]
                if name in ('Symbol','DateOfStartOfReportingPeriod','DateOfEndOfReportingPeriod','NatureOfReportStandaloneConsolidated','RevenueFromOperations','ProfitLossForPeriod','ProfitLossForPeriodFromContinuingOperations') and name in fs and fs[name].text!=e.text:conflict=True
                fs[name]=e
        if conflict:continue
        units={u.get('id'):[m.text for m in u.findall('.//x:measure',NS)] for u in root.findall('x:unit',NS)}
        for ctx,fs in facts.items():
            text=lambda n:fs[n].text.strip() if n in fs and fs[n].text else None
            if text('DateOfEndOfReportingPeriod')!=end or text('NatureOfReportStandaloneConsolidated')!='Consolidated' or not text('Symbol'):continue
            start=text('DateOfStartOfReportingPeriod')
            if not start or not 75<=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).days<=105:continue
            revenue=fs.get('RevenueFromOperations');profit=fs.get('ProfitLossForPeriod')
            if profit is None:profit=fs.get('ProfitLossForPeriodFromContinuingOperations')
            derived_profit=None
            if profit is None and 'ProfitLossForPeriodBeforeMinorityInterest' in fs and 'ShareOfProfitLossOfAssociates' in fs:
                group=fs['ProfitLossForPeriodBeforeMinorityInterest'];share=fs['ShareOfProfitLossOfAssociates']
                if units.get(group.get('unitRef'))==units.get(share.get('unitRef'))==['iso4217:INR']:
                    derived_profit=float(group.text)+float(share.text)
                    profit=group
            if revenue is None or profit is None or any(units.get(e.get('unitRef'))!=['iso4217:INR'] for e in (revenue,profit)):continue
            r,p=float(revenue.text),derived_profit if derived_profit is not None else float(profit.text)
            if not math.isfinite(r) or not math.isfinite(p) or r<=0 or abs(p/r)>=5:continue
            values.append({'symbol':symbol,'period_end':end,'revenue':r,'profit':p,'as_filed_symbol':text('Symbol')+'.NS',
                           'context':ctx,'source':row['source'],'document_file':row['document_file'],'document_sha256':row['document_sha256'],
                           'published_at':max(timestamp(t) for t in row['published_times']).isoformat()})
    return list({(v['document_sha256'],v['revenue'],v['profit']):v for v in values}.values())


def page_units(page):
    if re.search(r'(?i)(?:in\s*|[₹Rs.\s(])crores?[l)]?\b',page):return 1e7
    if re.search(r'(?i)(?:in\s*|[₹Rs.\s(])lakhs?\b',page):return 1e5
    if re.search(r'(?i)(?:\brupees\b|\bINR\b|₹|\bRs\.?).{0,30}millions?|millions?.{0,30}(?:\brupees\b|\bINR\b|₹|\bRs\.?)',page):return 1e6
    return None


def match_table(page,revenue,profit,end):
    """Both labelled values must share a printed numeric column in a scope page."""
    if not re.search('consolidated',page,re.I):return None
    factor=page_units(page)
    if factor is None:return None
    month=datetime.fromisoformat(end).strftime('%b').lower();year=end[:4]
    # Explicit quarter end must be visible, not inferred from the PDF filename.
    normalized=re.sub(r'[^a-z0-9]','',re.sub(r'(\d)(?:st|nd|rd|th)\b',r'\1',page.lower()))
    day=end[-2:];year2=year[-2:]
    forms=[day+month+year,day+month+year2,month+day+year,month+day+year2,
           day+end[5:7]+year,day+end[5:7]+year2]
    full=datetime.fromisoformat(end).strftime('%B').lower()
    forms += [day+full+year,day+full+year2,full+day+year,full+day+year2]
    if not any(f in normalized for f in forms):return None
    rev,pat=[],[]
    lines=page.splitlines()
    for i,line in enumerate(lines):
        lower=line.lower().replace('prollt','profit').replace('proflt','profit')
        # Join only an immediately wrapped label/value line; original positions
        # are retained in the evidence text for review.
        if i and re.search(r'profit.*after tax',lines[i-1],re.I):lower=lines[i-1].lower()+' '+lower
        is_revenue=bool(re.search(r'revenue\s*(?:from\s*operations|s\b|\b)',lower)) and 'per employee' not in lower
        is_profit=bool(re.search(r'(?:profit|loss).*(?:for\s*(?:the\s*)?(?:period|quarter|year)|after\s*tax|before\s*minority|before\s*non.controlling)',lower))
        if any(t in lower for t in ('before tax','before exceptional','other comprehensive','tax expenses','attributable to:')):is_profit=False
        for token in NUM.finditer(line):
            try:v=number(token.group())
            except ValueError:continue
            precision=2 if re.search(r'[.,]\d{2}\)?$',re.sub(r'\s','',token.group())) else 0
            tolerance=.5*10**(-precision)+1e-7
            if is_revenue and abs(v-revenue/factor)<tolerance:rev.append((i,token.end(),line))
            if is_profit and abs(v-profit/factor)<tolerance:pat.append((i,token.end(),line))
    pairs=[(r,p) for r in rev for p in pat if 0<p[0]-r[0]<60 and abs(r[1]-p[1])<=6]
    if not pairs:return None
    r,p=pairs[0]
    return {'unit_factor':factor,'revenue_line':r[2],'profit_line':p[2],'revenue_line_index':r[0],
            'profit_line_index':p[0],'revenue_column_end':r[1],'profit_column_end':p[1],
            'scope':'Consolidated','period_end':end}



def identity_proof(candidate,config,collection,directory):
    expected={r['symbol']:r['isin'] for r in config['symbols']}[candidate['symbol']]
    if candidate['isin']==expected:
        return {'basis':'Exact frozen ISIN in exchange announcement','isin':expected}
    legal=lambda name:re.sub(r'[^a-z0-9]','',re.sub(r'\blimited\b','ltd',name.lower()))
    name=candidate.get('issuer_name')
    if not name or candidate.get('exchange_symbol')!=candidate['symbol'].removesuffix('.NS'):return None
    printed=legal(Path(candidate['text_file']).read_text(errors='replace'))
    if legal(name) not in printed:return None
    # Current official master binds the same issuer name/symbol to the frozen
    # ISIN. This resolves issuer identity only, not past share/action valuation.
    master=directory/'reconciliation/indexes/equity_security_master.csv'
    if master.exists():
        import csv
        with master.open() as handle:
            for row in csv.DictReader(handle):
                if row.get('SYMBOL')==candidate['exchange_symbol'] and row.get(' ISIN NUMBER')==expected and legal(row.get('NAME OF COMPANY',''))==legal(name):
                    return {'basis':'Exact issuer legal name printed in original PDF and exact symbol/issuer/frozen ISIN in official current security master; historical index ISIN retained separately',
                            'isin':expected,'announcement_isin':candidate['isin'],'issuer_name':name,
                            'index_source':'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv',
                            'index_file':str(master.resolve()),'index_sha256':sha(master),
                            'limits':'Issuer financial identity only; no claim about historical entitlement or price comparability'}
    for receipt in collection['index_receipts']:
        path=directory/'raw'/receipt['sha256']
        if not path.exists() or sha(path)!=receipt['sha256']:continue
        rows=json.loads(path.read_text())
        if not isinstance(rows,list):continue
        for row in rows:
            if row.get('symbol')==candidate['symbol'].removesuffix('.NS') and row.get('isin')==expected and legal(row.get('companyName') or '')==legal(name):
                return {'basis':'Exact issuer legal name and exchange symbol printed in PDF and bound to frozen ISIN by official financial index; historical announcement ISIN differs',
                        'isin':expected,'announcement_isin':candidate['isin'],'issuer_name':name,'index_source':receipt['source'],'index_sha256':receipt['sha256']}
    return None


def run(directory=BASE):
    directory=Path(directory);root=directory/'reconciliation'
    collection=json.loads((directory/'collection.json').read_text())
    config=json.loads((ROOT/'config/financial_pilot_v1.json').read_text())
    identities={r['symbol']:r['isin'] for r in config['symbols']}
    accepted,pending=[],[]
    cases=sorted((root/'cases').glob('*.json'))
    for case_path in cases:
        case=json.loads(case_path.read_text());symbol,end=case['symbol'],case['period_end']
        raw=tentative_xml(collection,symbol,end)
        matches=[]
        for candidate in case['candidates']:
            binding=identity_proof(candidate,config,collection,directory)
            if not binding:continue
            if sha(candidate['pdf_file'])!=candidate['pdf_sha256'] or sha(candidate['index_file'])!=candidate['index_sha256'] or sha(candidate['text_file'])!=candidate['text_sha256']:raise RuntimeError('Reconciliation evidence changed')
            pages=Path(candidate['text_file']).read_text(errors='replace').split('\f')
            variants=[]
            for i,page in enumerate(pages,1):
                variants.append((i,page,None))
                ocr=root/'ocr'/f"{Path(candidate['pdf_file']).stem}_{i}.txt"
                if ocr.exists():variants.append((i,ocr.read_text(errors='replace'),str(ocr.resolve())))
                clean=root/'ocr'/f"{Path(candidate['pdf_file']).stem}_{i}_clean.txt"
                if clean.exists():variants.append((i,clean.read_text(errors='replace'),str(clean.resolve())))
            for x in raw:
                for i,page,ocr_file in variants:
                    proof=match_table(page,x['revenue'],x['profit'],end)
                    if proof:
                        published=max(timestamp(candidate['published_at']),timestamp(x['published_at']))
                        row={'symbol':symbol,'period_end':end,'revenue':x['revenue'],'profit':x['profit'],'margin':x['profit']/x['revenue'],
                             'scope':'Consolidated','currency':'INR','published_at':published.isoformat(),
                             'available_date':(published.date()+timedelta(days=1)).isoformat(),'first_seen_at':candidate['first_seen_at'],
                             'document_sha256':candidate['pdf_sha256'],'source':candidate['source'],'publication_source':candidate['index_source'],
                             'context':'Original PDF reconciled against explicit as-filed XML reporting metadata; invalid XML context not repaired',
                             'reconciliation':{'pdf':candidate,'identity':binding,'page':i,'table':proof,'xml':x,'ocr_file':ocr_file,'ocr_sha256':sha(ocr_file) if ocr_file else None,'review_status':'table_correlated_pending_review'}}
                        matches.append(row);break
        if matches:
            row=min(matches,key=lambda r:(r['published_at'],r['source']))
            accepted.append(row)
        else:pending.append({'symbol':symbol,'period_end':end,'xml_candidates':len(raw),'pdf_candidates':len(case['candidates']),
                             'reason':'No unambiguous quarter/unit/scope/row/column corroboration'})
    # These are candidates for visual review, not automatic additions to verified
    # coverage. Approval is recorded as evidence review, never a user permission.
    atomic_json(root/'corroboration_candidates.json',{'candidates':accepted,'pending':pending,'algorithm_sha256':sha(Path(__file__)),
                'pilot_config_sha256':sha(ROOT/'config/financial_pilot_v1.json'),'production_changed':False})
    print('Corroborated candidates:',len(accepted),'pending:',len(pending),'cases:',len(cases))
    for r in accepted:print(r['symbol'],r['period_end'],r['reconciliation']['page'],r['revenue']/1e7,r['profit']/1e7)
    return accepted,pending


if __name__=='__main__':run()
