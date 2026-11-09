"""Load explicit reviews of original financial tables without repairing XML.

A reviewed amount is in the PDF's printed unit. Every source byte, publication
receipt and issuer binding must still verify before it enters the pilot audit.
"""
from datetime import datetime, timedelta
import json
import math
from pathlib import Path

from .forward_tracking import IST
from .information import timestamp
from .model_tournament import sha
from .reconcile_pilot import identity_proof

ROOT = Path(__file__).resolve().parents[2]
REVIEWS = ROOT / 'config/pilot_reconciliation_reviews_v1.json'


def evidence_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_reviews(directory, config, collection, reviews=REVIEWS):
    """Return reviewed facts and all hashes needed to invalidate stale audits."""
    directory, config, reviews = Path(directory), Path(config), Path(reviews)
    if not reviews.exists():
        return [], {}, []
    ledger = json.loads(reviews.read_text())
    if ledger['pilot_config_sha256'] != sha(config):
        raise ValueError('Reviews belong to a different frozen pilot')
    protocol = json.loads(config.read_text())
    symbols = {r['symbol'] for r in protocol['symbols']}
    facts, sources, diagnostics, seen = [], {str(reviews): sha(reviews)}, [], set()
    for review in ledger['reviews']:
        key = review['symbol'], review['period_end']
        if key in seen or key[0] not in symbols:
            raise ValueError('Duplicate or ineligible reviewed quarter')
        seen.add(key)
        end = datetime.fromisoformat(key[1]).date()
        if end.month not in (3, 6, 9, 12) or (end + timedelta(days=1)).day != 1:
            raise ValueError('Review does not identify a quarter end')
        if review['status'] != 'reviewed_original_table' or review['scope'] != 'Consolidated' or review['currency'] != 'INR':
            raise ValueError('Unreviewed or incomparable financial table')
        unit = review['unit_factor']
        revenue, profit = review['reported_revenue'] * unit, review['reported_profit'] * unit
        if unit not in (1e5, 1e6, 1e7) or not all(math.isfinite(v) for v in (revenue, profit)) or revenue <= 0:
            raise ValueError('Invalid printed unit or amount')
        evidence = dict(review['evidence'])
        if (evidence['symbol'], evidence['period_end']) != key or evidence['source'] != review['source'] or evidence['pdf_sha256'] != review['pdf_sha256']:
            raise ValueError('Review/source identity mismatch')
        for name in ('pdf', 'text', 'index'):
            file = evidence_path(evidence[name + '_file'])
            expected = evidence[name + '_sha256']
            if sha(file) != expected:
                raise ValueError('Reviewed original evidence changed: ' + str(file))
            sources[str(file)] = expected
            evidence[name + '_file'] = str(file)
        pdf = Path(evidence['pdf_file']).read_bytes()
        if not pdf.startswith(b'%PDF') or b'%%EOF' not in pdf[-2048:]:
            raise ValueError('Incomplete original PDF')
        pages = Path(evidence['text_file']).read_text().split('\f')
        if not 1 <= review['page'] <= len(pages):
            raise ValueError('Invalid reviewed PDF page')
        publication = timestamp(evidence['published_at'])
        if evidence['index_source'].startswith('https://www.nseindia.com/api/corporate-announcements?'):
            index = json.loads(Path(evidence['index_file']).read_text())
            matched = [r for r in index['records'] if r.get('attchmntFile') == evidence['source']
                       and r.get('symbol') == evidence.get('exchange_symbol',key[0].removesuffix('.NS')) and r.get('sm_isin') == evidence['isin']]
            def disseminated(row):
                return max(datetime.strptime(row[k], '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST) for k in ('an_dt', 'exchdisstime'))
            if index['source'] != evidence['index_source'] or not any(disseminated(r) == publication for r in matched):
                raise ValueError('Publication is not bound to original exchange attachment')
        elif evidence.get('publication_precision'):
            # The versioned review pins the issuer HTML hash and conservative
            # end-of-day availability. It never invents an intraday timestamp.
            if (publication.hour, publication.minute, publication.second) != (23, 59, 59):
                raise ValueError('Day-only issuer publication must use end of day')
        else:
            raise ValueError('No verified publication receipt')
        if publication.date() <= end:
            raise ValueError('Financial publication predates quarter completion')
        binding = identity_proof(evidence, protocol, collection, directory)
        if not binding:
            raise ValueError('Reviewed issuer cannot be bound to frozen pilot identity')
        if binding.get('index_file'):
            sources[binding['index_file']] = binding['index_sha256']
        elif binding.get('index_sha256'):
            sources[str(directory / 'raw' / binding['index_sha256'])] = binding['index_sha256']
        facts.append({'symbol':key[0], 'period_end':key[1], 'revenue':revenue, 'profit':profit,
            'margin':profit / revenue, 'scope':'Consolidated', 'currency':'INR',
            'published_at':publication.isoformat(), 'available_date':(publication.date()+timedelta(days=1)).isoformat(),
            'first_seen_at':evidence['first_seen_at'], 'document_sha256':evidence['pdf_sha256'],
            'source':evidence['source'], 'context':'Reviewed original PDF quarterly statement; no XML context repaired',
            'reconciliation':{'page':review['page'], 'printed_unit':unit, 'review':review, 'identity':binding}})
        diagnostics.append({'symbol':key[0], 'period_end':key[1], 'status':'reconciled_original_pdf',
                            'source':evidence['source'], 'page':review['page']})
    return facts, sources, diagnostics
