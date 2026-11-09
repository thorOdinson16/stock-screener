"""Source-checked financial data readiness for the dashboard, stdlib only."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=ROOT/'ml/evaluation/results/financial_pilot_v1'


def digest(path):
    path=Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read_report(directory=None):
    directory=Path(directory) if directory else DEFAULT
    path=directory/'coverage_report.json'
    if not path.exists():return {'status':'not_available','evidence_current':False,'ready':False,'per_symbol':{},'production_changed':False}
    report=json.loads(path.read_text());reasons=[]
    if digest(ROOT/'config/financial_pilot_v1.json')!=report['pilot_config_sha256']:reasons.append('Pilot definition changed')
    for name,expected in report['artifacts'].items():
        if digest(directory/name)!=expected:reasons.append('Pilot dataset changed since audit')
    if digest(directory/'collection.json')!=report['provenance']['collection_sha256']:
        reasons.append('Filing collection changed since audit')
    else:
        collection=json.loads((directory/'collection.json').read_text())
        for record in collection['documents']:
            if digest(Path(record['document_file']))!=record['document_sha256']:
                reasons.append('Original filing evidence changed since audit')
                break
    for name,expected in report['provenance']['source_hashes'].items():
        if digest(ROOT/'ml/evaluation'/name)!=expected:reasons.append('Coverage definition changed since audit')
    for file,expected in report['provenance'].get('reconciliation_files',{}).items():
        if digest(file)!=expected:
            reasons.append('Reviewed PDF evidence changed since audit')
            break
    gate=report['readiness']
    return {'status':'stale_evidence' if reasons else report['status'],'evidence_current':not reasons,
            'ready':not reasons and gate['ready'],'stale_reasons':reasons,'readiness':gate,'per_symbol':report['per_symbol'],
            'reconciled_quarters':report.get('reconciled_quarters',0),'accepted_filings':report['accepted_filings'],'rejected_filings':report['rejected_filings'],
            'missing_quarters':report['missing_quarters'],'source_errors':report['source_errors'],
            'symbols':[r['symbol'] for r in report['protocol']['symbols']],'limits':report['limits'],'production_changed':False}
