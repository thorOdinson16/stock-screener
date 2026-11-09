"""Collect original NSE information into a separate append-only research archive."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ml.evaluation.forward_tracking import atomic_json, IST


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def exclusive(path, value):
    import os
    import tempfile
    payload = json.dumps(value, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(payload)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass  # First observation/version remains immutable.
    finally:
        os.unlink(temporary)


def main():
    from curl_cffi import requests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'ml/evaluation/results/information_archive')
    parser.add_argument('--history-symbols', type=int, default=60)
    parser.add_argument('--max-downloads', type=int, default=420)
    args = parser.parse_args()
    now = datetime.now(IST)
    observed = now.isoformat()
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    universe = json.loads((ROOT / 'poller/universe/universe_cache.json').read_text())['stocks']
    symbols = sorted(s['yf_symbol'] for s in universe)
    eligible = set(symbols)
    session = requests.Session(impersonate='chrome')
    receipts = []
    def retrieve(url):
        response = session.get(url, timeout=40)
        response.raise_for_status()
        raw = response.content
        sha = digest(raw)
        path = root / 'raw' / sha
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
        receipts.append({'source': url, 'retrieved_at': datetime.now(IST).isoformat(), 'sha256': sha,
                         'bytes': len(raw), 'status': 'complete'})
        return raw
    records = []
    # Existing original documents retain their individual publication evidence.
    old = ROOT / 'ml/evaluation/results/remaining_plan_20261108/filings'
    legacy = json.loads((old / 'downloads.json').read_text())['receipts'] if (old / 'downloads.json').exists() else []
    for row in legacy:
        if row['status'] == 'downloaded':
            records.append((row, old / 'xml' / row['file']))
    queue = []
    fiscal_source = 'https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly&from_date=01-01-2022&to_date=' + now.strftime('%d-%m-%Y')
    if args.history_symbols > 0:
        try:
            raw = retrieve(fiscal_source)
            data = json.loads(raw)
            history_symbols = set(symbols[:args.history_symbols])  # alphabetical, never outcome-selected
            already = {r['source'] for r, _ in records}
            for row in data:
                symbol = row.get('symbol', '') + '.NS'
                url = row.get('xbrl') or ''
                if symbol not in history_symbols or row.get('consolidated') != 'Consolidated' or row.get('cumulative') != 'Non-cumulative' or not url.endswith('.xml') or url in already:
                    continue
                queue.append({'symbol': symbol, 'period_end': row['toDate'],
                              'published_times': [row['broadCastDate'], row['exchdisstime']],
                              'source': url, 'index_source': fiscal_source})
            queue = sorted(queue, key=lambda r: (r['symbol'], r['period_end'], r['source']))
        except Exception as e:
            receipts.append({'source': fiscal_source, 'status': 'error', 'reason': str(e)[:250]})
    rss_source = 'https://nsearchives.nseindia.com/content/RSS/Integrated_Filing_Financials.xml'
    try:
        feed = ET.fromstring(retrieve(rss_source))
        # Symbol comes from XML identity, never fuzzy company-name matching.
        for item in feed.findall('./channel/item'):
            link = item.findtext('link') or ''
            if link.startswith('https://nsearchives.nseindia.com/corporate/') and link.endswith('.xml'):
                queue.insert(0, {'source': link, 'published_times': [item.findtext('pubDate')], 'index_source': rss_source})
    except Exception as e:
        receipts.append({'source': rss_source, 'status': 'error', 'reason': str(e)[:250]})
    unique = {r['source']: r for r in queue}
    selected = list(unique.values())[:args.max_downloads]
    def download(row):
        url = row['source']
        if not url.startswith('https://nsearchives.nseindia.com/corporate/'):
            return None
        cache = root / 'documents' / digest(url.encode())
        cache.parent.mkdir(exist_ok=True)
        try:
            if not cache.exists():
                response = requests.get(url, impersonate='chrome', timeout=15)
                response.raise_for_status()
                if b'xbrl' not in response.content[:4000].lower():
                    raise ValueError('Original XBRL unavailable')
                cache.write_bytes(response.content)
            return row, cache
        except Exception as e:
            return {'source': url, 'reason': str(e)[:200]}
    failures = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i, result in enumerate(executor.map(download, selected), 1):
            if isinstance(result, tuple):
                records.append(result)
            elif result:
                failures.append(result)
            if i % 50 == 0:
                print(f'INFORMATION documents {i}/{len(selected)}', flush=True)
    # Parse under the research env in the next stage; collector itself needs no ML libs.
    parsed_records = []
    for row, path in records:
        body = path.read_bytes()
        sha = digest(body)
        target = root / 'raw' / sha
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            target.write_bytes(body)
        identity = digest(json.dumps([row['source'], sha, row['published_times']], sort_keys=True).encode())
        item = {**row, 'first_seen_at': datetime.now(IST).isoformat(), 'document_sha256': sha,
                'document_file': str(target.resolve()), 'record_id': identity}
        exclusive(root / 'filings' / f'{identity}.json', item)
        parsed_records.append(identity)
    announcements_source = 'https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=' + (now - timedelta(days=7)).strftime('%d-%m-%Y') + '&to_date=' + now.strftime('%d-%m-%Y')
    announcement_count = 0
    try:
        data = json.loads(retrieve(announcements_source))
        for row in data:
            symbol = row.get('symbol', '') + '.NS'
            if symbol not in eligible or not row.get('exchdisstime'):
                continue
            try:
                published = max(datetime.strptime(row[k], '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST) for k in ('an_dt','exchdisstime'))
                if published > now:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            # Store raw observed subject only. Provider voice summaries are not
            # treated as timestamped original financial facts or sentiment.
            event = {'symbol': symbol, 'published_at': published.isoformat(), 'first_seen_at': datetime.now(IST).isoformat(),
                     'subject': row.get('desc') or '', 'source': row.get('attchmntFile'),
                     'publication_source': announcements_source, 'exchange_id': row.get('seq_id'),
                     'raw_record_sha256': digest(json.dumps(row, sort_keys=True).encode())}
            identity = digest(json.dumps([event['exchange_id'],event['raw_record_sha256']],sort_keys=True).encode())
            exclusive(root / 'announcements' / f'{identity}.json', event)
            announcement_count += 1
    except Exception as e:
        receipts.append({'source': announcements_source, 'status': 'error', 'reason': str(e)[:250]})
    run = {'observed_at': observed, 'receipts': receipts, 'announcement_rows_this_run': announcement_count,
           'filing_versions_this_run': len(parsed_records), 'new_download_attempts': len(selected), 'download_failures': failures,
           'history_selection': f'First {args.history_symbols} alphabetically in current universe plus existing verified corpus and current integrated RSS; budget {args.max_downloads}',
           'coverage_claim': 'Partial archive; absence is not proof that no announcement occurred', 'production_changed': False}
    stamp = now.strftime('%Y%m%dT%H%M%S%f')
    exclusive(root / 'collections' / f'{stamp}.json', run)
    atomic_json(root / 'latest_collection.json', run)
    print(json.dumps({k: run[k] for k in ('announcement_rows_this_run','filing_versions_this_run','new_download_attempts')}) , flush=True)


if __name__ == '__main__':
    main()
