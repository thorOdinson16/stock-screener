"""Timestamped information features. No inferred publication dates or consensus."""
from datetime import datetime, timedelta
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from .forward_tracking import IST
from .verified_financials import NS

EARNINGS_FEATURES = ['info_margin', 'info_revenue_qoq', 'info_profit_change_on_revenue',
                     'info_revenue_yoy', 'info_margin_change', 'info_filing_age']
SECTOR_FEATURES = ['peer_breadth_200d', 'peer_breadth_21d', 'peer_return_21d',
                   'peer_return_dispersion_63d', 'stock_minus_peer_21d', 'peer_volume_expansion']
EVENT_FEATURES = ['announcement_count_7d', 'results_count_7d', 'orders_count_7d', 'governance_count_7d']


def timestamp(value):
    if not value:
        raise ValueError('Explicit publication timestamp required')
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        result = datetime.strptime(value, '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(IST)


def document_facts(record, path, observed):
    """Require identity, consolidated whole-company quarter and INR units."""
    raw = Path(path).read_bytes()
    root = ET.fromstring(raw)
    published = max(timestamp(t) for t in record['published_times'])
    seen = timestamp(observed)
    if published > seen:
        raise ValueError('Future publication')
    facts = {}
    for e in root:
        if e.get('contextRef'):
            facts.setdefault(e.get('contextRef'), {})[e.tag.split('}')[-1]] = e
    text = lambda fs, n: fs[n].text.strip() if n in fs and fs[n].text else None
    candidates = [(k, f) for k, f in facts.items() if text(f, 'Symbol') and text(f, 'DateOfEndOfReportingPeriod') and text(f, 'NatureOfReportStandaloneConsolidated') == 'Consolidated']
    # Primary metadata must identify a real quarter context, never a YTD segment.
    units = {u.get('id'): [m.text for m in u.findall('.//x:measure', NS)] for u in root.findall('x:unit', NS)}
    contexts = {c.get('id'): c for c in root.findall('x:context', NS)}
    choices = []
    for key, fs in candidates:
        c = contexts.get(key)
        if c is None or c.findall('.//d:explicitMember', NS) or c.findall('.//d:typedMember', NS):
            continue
        start, end = c.find('x:period/x:startDate', NS), c.find('x:period/x:endDate', NS)
        if start is None or end is None or not 75 <= (datetime.fromisoformat(end.text) - datetime.fromisoformat(start.text)).days <= 105:
            continue
        if text(fs, 'DateOfEndOfReportingPeriod') != end.text:
            continue
        revenue = fs.get('RevenueFromOperations')
        profit = fs.get('ProfitLossForPeriod')
        if profit is None:
            profit = fs.get('ProfitLossForPeriodFromContinuingOperations')
        if revenue is None or profit is None or any(units.get(e.get('unitRef')) != ['iso4217:INR'] for e in (revenue, profit)):
            continue
        r, p = float(revenue.text), float(profit.text)
        if math.isfinite(r) and math.isfinite(p) and r > 0 and abs(p / r) < 5:
            choices.append((key, fs, r, p, end.text))
    if len(choices) != 1:
        raise ValueError('Unique consolidated quarterly INR context required')
    key, fs, revenue, profit, end = choices[0]
    symbol = text(fs, 'Symbol') + '.NS'
    if record.get('symbol') and symbol != record['symbol']:
        raise ValueError('Symbol disagrees with exchange index')
    if end >= published.date().isoformat():
        raise ValueError('Reporting end does not precede publication')
    if record.get('period_end'):
        expected = datetime.strptime(record['period_end'], '%d-%b-%Y').date().isoformat()
        if expected != end:
            raise ValueError('Reporting period disagrees with index')
    return {'symbol': symbol, 'period_end': end, 'revenue': revenue, 'profit': profit,
            'margin': profit / revenue, 'scope': 'Consolidated', 'currency': 'INR',
            'published_at': published.isoformat(), 'first_seen_at': seen.isoformat(),
            'available_date': (published.date() + timedelta(days=1)).isoformat(),
            'document_sha256': hashlib.sha256(raw).hexdigest(), 'source': record['source'],
            'publication_source': record['index_source'], 'context': key}


def earnings_at(rows, day, observed_cutoff=None):
    eligible = [r for r in rows if r['available_date'] <= day and
                (observed_cutoff is None or timestamp(r['first_seen_at']) <= timestamp(observed_cutoff))]
    result = {k: np.nan for k in EARNINGS_FEATURES}
    if not eligible:
        return result
    # A late amendment of an old quarter must not replace a newer fiscal quarter.
    eligible = sorted(eligible, key=lambda r: (r['period_end'], r['published_at'], r['document_sha256']))
    latest = eligible[-1]
    age = (datetime.fromisoformat(day) - datetime.fromisoformat(latest['period_end'])).days
    if not 0 <= age <= 180:
        return result
    periods = {r['period_end']: r for r in eligible}
    q = pd.Period(latest['period_end'], freq='Q')
    previous = periods.get((q - 1).end_time.date().isoformat())
    year = periods.get((q - 4).end_time.date().isoformat())
    result.update(info_margin=latest['margin'], info_filing_age=float(age))
    if previous and previous['scope'] == latest['scope'] and previous['currency'] == latest['currency']:
        result.update(info_revenue_qoq=latest['revenue'] / previous['revenue'] - 1,
                      info_profit_change_on_revenue=(latest['profit'] - previous['profit']) / previous['revenue'],
                      info_margin_change=latest['margin'] - previous['margin'])
    if year and year['scope'] == latest['scope'] and year['currency'] == latest['currency']:
        result['info_revenue_yoy'] = latest['revenue'] / year['revenue'] - 1
    return result


def attach_earnings(frame, records, observed_cutoff=None, feature_day=None):
    result = frame.copy()
    by_symbol = {}
    for r in records:
        by_symbol.setdefault(r['symbol'], []).append(r)
    # Cache across hundreds of stock rows on the same dates. Only records public
    # by that date contribute; later restatements cannot rewrite early features.
    cache = {}
    features = []
    for row in frame[['symbol', 'trade_date']].itertuples(index=False):
        day = feature_day or row.trade_date
        key = (row.symbol, day)
        if key not in cache:
            cache[key] = earnings_at(by_symbol.get(row.symbol, []), day, observed_cutoff)
        features.append(cache[key])
    return pd.concat([result.reset_index(drop=True), pd.DataFrame(features, columns=EARNINGS_FEATURES)], axis=1)


def sector_features(frame, bars, sessions):
    """Leave-one-out peer breadth/dispersion; >=5 other stocks, no fills."""
    parts = []
    for symbol, group in frame.groupby('symbol', sort=False):
        close = pd.Series([bars.get((symbol, d), {}).get('close') for d in sessions], index=sessions, dtype=float).where(lambda s: s > 0)
        volume = pd.Series([bars.get((symbol, d), {}).get('volume') for d in sessions], index=sessions, dtype=float)
        sma = close.rolling(200, min_periods=200).mean()
        ret21 = (close / close.shift(21) - 1).where(close.rolling(22, min_periods=22).count() == 22)
        ret63 = (close / close.shift(63) - 1).where(close.rolling(64, min_periods=64).count() == 64)
        volume_ratio = volume.rolling(5, min_periods=5).mean() / volume.rolling(20, min_periods=20).mean().where(lambda s: s > 0)
        data = pd.DataFrame({'r21': ret21, 'r63': ret63,
                             'above200': (close > sma).astype(float).where(sma.notna()),
                             'positive21': (ret21 > 0).astype(float).where(ret21.notna()),
                             'vr': volume_ratio}, index=sessions).reindex(group.trade_date)
        data['symbol'], data['trade_date'] = symbol, group.trade_date.to_numpy()
        parts.append(data.reset_index(drop=True))
    result = frame.merge(pd.concat(parts, ignore_index=True), on=['symbol', 'trade_date'], validate='one_to_one')
    for raw, out in [('above200','peer_breadth_200d'),('positive21','peer_breadth_21d'),('r21','peer_return_21d'),('vr','peer_volume_expansion')]:
        groups = result.groupby(['trade_date', 'industry'], dropna=False)[raw]
        count = groups.transform('count') - result[raw].notna().astype(int)
        total = groups.transform('sum') - result[raw].fillna(0.)
        result[out] = (total / count.where(count > 0)).where(count >= 5)
    groups = result.groupby(['trade_date','industry'], dropna=False).r63
    count = groups.transform('count') - result.r63.notna().astype(int)
    total = groups.transform('sum') - result.r63.fillna(0.)
    squares = result.r63.pow(2).groupby([result.trade_date,result.industry], dropna=False).transform('sum') - result.r63.fillna(0.).pow(2)
    result['peer_return_dispersion_63d'] = np.sqrt(((squares - total.pow(2) / count.where(count > 0)) / (count - 1).where(count > 1)).clip(lower=0)).where(count >= 5)
    result['stock_minus_peer_21d'] = result.r21 - result.peer_return_21d
    return result.drop(columns=['r21','r63','above200','positive21','vr'])


def announcement_features(records, symbol, cutoff, coverage_start):
    """Use publication AND actual observation; no archive coverage => unknown."""
    end = timestamp(cutoff)
    start = end - timedelta(days=7)
    if coverage_start is None or timestamp(coverage_start) > start:
        return {k: None for k in EVENT_FEATURES}
    # A healthy collection window must be supplied by the caller, not inferred
    # from absence of rows. Caller tracks errors and gaps in collection receipts.
    known = [r for r in records if r['symbol'] == symbol and start <= timestamp(r['published_at']) <= end and timestamp(r['first_seen_at']) <= end]
    versions = {}
    for row in known:
        key = (row.get('exchange_id') or row.get('source'), row['published_at'])
        if key not in versions or row['first_seen_at'] > versions[key]['first_seen_at']:
            versions[key] = row
    known = list(versions.values())
    categories = {'results_count_7d': ('financial result', 'results'), 'orders_count_7d': ('order','contract'),
                  'governance_count_7d': ('resignation','appointment','director','management')}
    result = {'announcement_count_7d': len(known)}
    for name, words in categories.items():
        result[name] = sum(any(w in r['subject'].lower() for w in words) for r in known)
    return result
