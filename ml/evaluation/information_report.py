"""Read-only information trial report; no training libraries required by API."""
import hashlib
import json
from pathlib import Path
from functools import lru_cache

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / 'ml/evaluation/results/information_research_20261109'


@lru_cache(maxsize=8192)
def _cached_hash(path, size, modified):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(path):
    path = Path(path)
    if not path.is_file():
        return None
    stat = path.stat()
    return _cached_hash(str(path), stat.st_size, stat.st_mtime_ns)


def read_report(directory=None):
    directory = Path(directory) if directory else DEFAULT
    file = directory / 'report.json'
    if not file.exists():
        return {'status':'not_available','evidence_current':False,'stale_reasons':[],
                'results':{},'paper':{},'production_changed':False}
    report = json.loads(file.read_text())
    protocol = report['protocol']
    reasons = []
    if json.loads((directory/'protocol.json').read_text()) != protocol:
        reasons.append('Completed report protocol changed')
    for name,expected in protocol['source_hashes'].items():
        if digest(ROOT/'ml/evaluation'/name) != expected:
            reasons.append(f'Frozen research source changed: {name}')
    for name,expected in protocol['filing_files'].items():
        if digest(Path(name)) != expected:
            reasons.append('Original filing evidence changed')
            break
    base = ROOT / 'ml/evaluation/results/model_tournament_20261108/protocol.json'
    if digest(base) != protocol['base_protocol']:
        reasons.append('Base historical protocol unavailable or changed')
    else:
        inputs = Path(json.loads(base.read_text())['input_directory'])
        manifest = inputs/'manifest.json'
        if digest(manifest) != protocol['input_manifest']:
            reasons.append('Historical snapshot manifest changed')
        else:
            metadata = json.loads(manifest.read_text())
            for name,expected in metadata['files'].items():
                if digest(inputs/name) != expected:
                    reasons.append(f'Historical data changed: {name}')
            for name,expected in metadata['source_hashes'].items():
                if digest(ROOT/name) != expected:
                    reasons.append(f'Historical feature source changed: {name}')
    frozen = json.loads((directory/'paper_models.json').read_text())
    if digest(directory/'protocol.json') != frozen['protocol_sha256']:
        reasons.append('Frozen paper protocol changed')
    for entry in frozen['models'].values():
        if digest(directory/entry['file']) != entry['sha256']:
            reasons.append('Frozen paper weights changed')
    results = {}
    for name,periods in report['results'].items():
        results[name] = {}
        for period,value in periods.items():
            results[name][period] = {'retail':value['retail'],'matched_momentum_retail':value['matched_momentum_retail'],
                'equal_notional':{k:v for k,v in value['selection_diagnostic']['strategies']['model'].items() if k!='cycles'}}
    paper_file = directory/'paper/status.json'
    paper = json.loads(paper_file.read_text()) if paper_file.exists() else {'status':'not_started','matured_cohorts':0}
    return {'status':'stale_evidence' if reasons else report['status'],'evidence_current':not reasons,'stale_reasons':reasons,
        'production_changed':False,'promotion_allowed':False,'selected_on_validation':report['selected_on_validation'],
        'results':results,'filing_coverage':report['filing_coverage'],'feature_coverage':report['feature_coverage'],
        'paper':paper,'announcements':{'status':'collection_only','reason':'Continuous observation coverage and matured training outcomes are not yet established'},
        'limits':report['limits']}
