"""Read-only attribution of frozen ranking failures; no search or selection."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import prepare, sha, signals_for
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.corporate_actions import load_events
from ml.evaluation.retail import Policy, simulate, eligibility
from ml.evaluation.research_context import portfolio_robustness


def ranking_metrics(frame, minimum=20):
    """Signal-date metrics; unknown outcomes remain counted, never rank-filtered."""
    daily = []
    for day, part in frame.groupby('trade_date', sort=True):
        known = part[part.net_return.notna()]
        ic = known.score.corr(known.net_return, method='spearman') if len(known) >= minimum and known.score.nunique() > 1 and known.net_return.nunique() > 1 else np.nan
        legacy = part[part.legacy_target.notna()]
        legacy_ic = legacy.score.corr(legacy.legacy_target, method='spearman') if len(legacy) >= minimum and legacy.score.nunique() > 1 and legacy.legacy_target.nunique() > 1 else np.nan
        # Select BEFORE looking at outcome availability. No replacements.
        selected = part.sort_values(['score', 'symbol'], ascending=[False, True]).head(20)
        top = float(selected.net_return.mean()) if len(selected) and selected.net_return.notna().all() else None
        pool = float(part.net_return.mean()) if len(part) and part.net_return.notna().all() else None
        daily.append({'trade_date': day, 'rank_ic': float(ic) if np.isfinite(ic) else None,
                      'legacy_rank_ic': float(legacy_ic) if np.isfinite(legacy_ic) else None,
                      'top20_net_mean': top, 'eligible_pool_net_mean': pool,
                      'top20_minus_pool': top - pool if top is not None and pool is not None else None,
                      'selected_count': len(selected), 'selected_unknown': int(selected.net_return.isna().sum())})
    values = lambda field: [r[field] for r in daily if r[field] is not None]
    ics = values('rank_ic')
    return {'signal_rows': len(frame), 'known_execution_rows': int(frame.net_return.notna().sum()),
            'outcome_coverage': float(frame.net_return.notna().mean()) if len(frame) else None,
            'signal_dates': len(daily), 'ic_dates': len(ics),
            'mean_daily_rank_ic': float(np.mean(ics)) if ics else None,
            'positive_ic_date_fraction': float(np.mean(np.asarray(ics) > 0)) if ics else None,
            'mean_legacy_rank_ic': float(np.mean(values('legacy_rank_ic'))) if values('legacy_rank_ic') else None,
            'mean_top20_net': float(np.mean(values('top20_net_mean'))) if values('top20_net_mean') else None,
            'mean_top20_minus_pool': float(np.mean(values('top20_minus_pool'))) if values('top20_minus_pool') else None,
            'complete_top20_dates': len(values('top20_net_mean')), 'complete_spread_dates': len(values('top20_minus_pool')),
            'minimum_cross_section': minimum, 'daily': daily}


def trade_attribution(simulation):
    """Exact gross/net bridge on the SAME closed trades, quantities and exits."""
    p = simulation['policy']
    slip = p['slippage_bps'] / 10000
    capital = p['capital']
    rows = []
    for trade in simulation['trades']:
        entry = trade['entry_price'] / (1 + slip)
        exit_price = trade['exit_price'] / (1 - slip)
        gross = trade['quantity'] * (exit_price - entry)
        rows.append({'symbol': trade['symbol'], 'industry': trade['industry'], 'exit_date': trade['exit_date'],
                     'gross_pnl': gross, 'net_pnl': trade['pnl'], 'cost_drag': gross - trade['pnl'],
                     'stop': trade['exit_reason'] == 'stop', 'cost': trade['cost'], 'proceeds': trade['proceeds']})
    def summary(part):
        return {'closed_trades': len(part), 'gross_pnl': float(sum(r['gross_pnl'] for r in part)),
                'net_pnl': float(sum(r['net_pnl'] for r in part)),
                'cost_drag': float(sum(r['cost_drag'] for r in part)),
                'net_contribution_on_initial_capital': float(sum(r['net_pnl'] for r in part) / capital),
                'stop_fraction': float(np.mean([r['stop'] for r in part])) if part else None,
                'turnover_on_initial_capital': float(sum(r['cost'] + r['proceeds'] for r in part) / capital)}
    result = summary(rows)
    result['status'] = 'complete' if simulation['status'] == 'complete' and not simulation['open_positions'] else 'closed_trade_prefix'
    result['by_sector'] = {sector: summary([r for r in rows if r['industry'] == sector]) for sector in sorted(set(r['industry'] for r in rows))}
    result['by_exit_quarter'] = {q: summary([r for r in rows if str(pd.Period(r['exit_date'], freq='Q')) == q]) for q in sorted(set(str(pd.Period(r['exit_date'], freq='Q')) for r in rows))}
    return result


def summarize_groups(frame, column, minimum):
    return {str(key): {k: v for k, v in ranking_metrics(part, minimum).items() if k != 'daily'}
            for key, part in frame.groupby(column, dropna=False, sort=True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research', type=Path, default=ROOT / 'ml/evaluation/results/xgb_kalman_20261109')
    parser.add_argument('--output', type=Path, default=ROOT / 'ml/evaluation/results/prediction_diagnosis_20261109')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report_file = args.output / 'report.json'
    if report_file.exists():
        print('Completed diagnosis is frozen; choose a new output')
        return
    research = json.loads((args.research / 'report.json').read_text())
    spec = research['protocol']
    source = Path(spec['input_directory'])
    base = Path(spec['base_directory'])
    if json.loads((args.research / 'protocol.json').read_text()) != spec:
        raise RuntimeError('Research protocol differs')
    for name, digest in spec['source_hashes'].items():
        if sha(ROOT / 'ml/evaluation' / name) != digest:
            raise RuntimeError(f'Research source changed: {name}')
    if sha(source / 'manifest.json') != spec['input_manifest'] or sha(base / 'protocol.json') != spec['base_protocol']:
        raise RuntimeError('Research inputs changed')
    selection = json.loads((args.research / 'selection.json').read_text())
    reference = Path(json.loads((base / 'protocol.json').read_text())['reference_directory'])
    files = {str(args.research / 'report.json'): sha(args.research / 'report.json')}
    predictions = {}
    for name in research['results']:
        later = args.research / f'{name}_assessment_signals.parquet'
        files[str(later)] = sha(later)
        assessment = pd.read_parquet(later)
        if name == 'kalman_trend':
            # Original validation signals are deterministic, past-only filter outputs.
            continue
        if name.startswith('xgb_'):
            target = name[len('xgb_'):]
            config = selection['metadata'][name]['config']
            paths = [args.research / 'predictions' / f'xgb_{target}_config{config}_fold{f}.parquet' for f in (1, 2, 3)]
        elif name == 'current_model_reference':
            paths = [reference / f'rank21_fold{f}.csv' for f in (1, 2, 3)]
        else:
            meta = json.loads((base / 'selection.json').read_text())['metadata'][name]
            paths = [base / 'predictions' / f"{meta['checkpoint_name']}_fold{f}.parquet" for f in (1, 2, 3)]
        older = []
        for f, path in enumerate(paths, 1):
            files[str(path)] = sha(path)
            part = pd.read_csv(path) if path.suffix == '.csv' else pd.read_parquet(path)
            part['fold'] = f
            older.append(part)
        predictions[name] = pd.concat(older + [assessment], ignore_index=True)
    # Source files and outputs are pinned before computing attribution.
    protocol = {'research': str(args.research.resolve()), 'files': files,
                'source_hashes': {n: sha(ROOT / 'ml/evaluation' / n) for n in
                                  ('diagnose_predictions.py', 'model_tournament.py', 'retail.py', 'research_context.py', 'xgb_kalman.py')},
                'purpose': 'Descriptive attribution only; no candidate selection, model fitting or parameter optimization',
                'groups': 'Fixed calendar quarters, existing industries, signal-date market return63 sign and volatility20 above pre-validation median',
                'ranking': 'Equal weight per signal date; outcome coverage retained; minimum 20 stocks overall, 5 within sector; sector samples under 20 IC dates flagged',
                'costs': 'Both exact closed-trade gross/net accounting and a zero-cost re-simulation; re-simulation may change holdings',
                'allocation': 'Same-schedule top20 equal-notional versus constrained equal-target basket; daily ten-position planner shown separately'}
    protocol_path = args.output / 'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != protocol:
        raise RuntimeError('Diagnosis protocol changed')
    atomic_json(protocol_path, protocol)
    frame, bars, sessions, statistics, _ = prepare(source, base)
    events = load_events()
    # Recover Kalman on exactly the frozen common keys, without fitting or tuning.
    from ml.evaluation.xgb_kalman import build_kalman
    base_protocol = json.loads((base / 'protocol.json').read_text())
    reference_keys = []
    for f in range(1, 6):
        path = reference / f'rank21_fold{f}.csv'
        if sha(path) != base_protocol['reference_files'][path.name]:
            raise RuntimeError('Reference signals changed')
        part = pd.read_csv(path)[['symbol', 'trade_date']]
        part['fold'] = f
        reference_keys.append(part)
    keys = pd.concat(reference_keys, ignore_index=True)
    kalman = build_kalman(frame, bars, sessions, events).merge(keys, on=['symbol', 'trade_date'], validate='one_to_one')
    keys = kalman[['symbol', 'trade_date', 'fold']]
    predictions = {name: p.merge(keys, on=['symbol', 'trade_date', 'fold'], validate='one_to_one') for name, p in predictions.items()}
    predictions['kalman_trend'] = kalman
    momentum = keys.copy()
    momentum['score'] = [statistics[(s, d)]['momentum'] for s, d in zip(keys.symbol, keys.trade_date)]
    predictions['momentum'] = momentum
    if any(len(p) != len(keys) for p in predictions.values()):
        raise RuntimeError('Inference pools differ')
    market = pd.DataFrame({'trade_date': sessions, 'close': [bars[('^NSEI', d)]['close'] for d in sessions]})
    market['market_return_1d'] = market.close.pct_change(fill_method=None)
    market['market_return_63d'] = market.close.pct_change(63, fill_method=None)
    market['market_trend_200d'] = market.close / market.close.rolling(200).mean() - 1
    market['market_volatility_20d'] = market.market_return_1d.rolling(20).std()
    cutoff = market.loc[market.trade_date < research['periods']['validation'][0], 'market_volatility_20d'].median()
    market['trend_regime'] = np.where(market.market_return_63d >= 0, 'positive_trailing_63d', 'negative_trailing_63d')
    market['vol_regime'] = np.where(market.market_volatility_20d > cutoff, 'higher_volatility', 'lower_volatility')
    labels = frame[['symbol', 'trade_date', 'industry', 'net_return', 'scheduled_exit_date', 'excess_ret_21d', 'legacy_maturity']].copy()
    results = {}
    for name, prediction in predictions.items():
        results[name] = {}
        for period, (start, end) in research['periods'].items():
            print(f'DIAGNOSE {name} {period}', flush=True)
            p = prediction[prediction.fold.le(3) if period == 'validation' else prediction.fold.ge(4)].copy()
            eligible = [not eligibility({**statistics[(s, d)], 'rank': 1}, Policy(), d, events) for s, d in zip(p.symbol, p.trade_date)]
            p = p.loc[eligible]
            joined = p.merge(labels, on=['symbol', 'trade_date'], validate='one_to_one')
            joined.loc[joined.scheduled_exit_date.isna() | (joined.scheduled_exit_date > end), 'net_return'] = np.nan
            joined['legacy_target'] = joined.excess_ret_21d.where(joined.legacy_maturity.notna() & (joined.legacy_maturity <= end))
            joined['quarter'] = pd.to_datetime(joined.trade_date).dt.to_period('Q').astype(str)
            joined = joined.merge(market.drop(columns='close'), on='trade_date', validate='many_to_one')
            ranking = ranking_metrics(joined)
            sectors = summarize_groups(joined, 'industry', 5)
            for value in sectors.values():
                value['small_sample'] = value['ic_dates'] < 20
            signals = signals_for(p, statistics, events)
            base_sim = simulate(signals, bars, sessions, Policy(), events, start, end)
            zero = simulate(signals, bars, sessions, replace(Policy(), fee_bps=0, slippage_bps=0), events, start, end)
            # Base results must reproduce exactly before drawing conclusions.
            original_name = 'kalman_trend' if name == 'kalman_trend' else name
            if name != 'momentum':
                expected = research['results'][original_name][period]['retail']['base']
                if base_sim['status'] != expected['status'] or base_sim['net_return'] != expected['net_return']:
                    raise RuntimeError(f'Base portfolio does not reproduce: {name} {period}')
                detail = json.loads((args.research / f'{name}_{period}.json').read_text())
            else:
                detail = json.loads((args.research / f'xgb_execution_{period}.json').read_text())
                expected = research['results']['xgb_execution'][period]['matched_momentum_retail']['base']
                if base_sim['status'] != expected['status'] or base_sim['net_return'] != expected['net_return']:
                    raise RuntimeError('Momentum does not reproduce')
            strategy = 'momentum' if name == 'momentum' else 'model'
            basket = detail['baskets']['strategies'][strategy]
            shadow = detail['baskets']['selection_diagnostic']['strategies'][strategy]
            results[name][period] = {
                'ranking': ranking, 'ranking_by_quarter': summarize_groups(joined, 'quarter', 20),
                'ranking_by_sector': sectors, 'ranking_by_trend_regime': summarize_groups(joined, 'trend_regime', 20),
                'ranking_by_volatility_regime': summarize_groups(joined, 'vol_regime', 20),
                'portfolio': {'status': base_sim['status'], 'blocked': base_sim['blocked'], 'net_return': base_sim['net_return'],
                              'zero_cost_status': zero['status'], 'zero_cost_net_return': zero['net_return'],
                              'closed_trade_accounting': trade_attribution(base_sim),
                              'robustness': portfolio_robustness(base_sim, market, cutoff)},
                'same_schedule_allocation': {'equal_notional': {k: shadow[k] for k in ('status', 'net_return', 'complete_cycles')},
                                             'constrained_equal_target': {k: basket[k] for k in ('status', 'net_return', 'mean_entry_exposure', 'closed_trades')},
                                             'cycles': shadow['cycles']}}
    report = {'status': 'historical_diagnosis', 'production_changed': False, 'selection_performed': False,
              'protocol': protocol, 'periods': research['periods'], 'results': results,
              'limits': ['Period/sector/regime slices are retrospective, descriptive and correlated; no significance or trading gate inferred.',
                         'Signal-quarter IC uses later matured outcomes; portfolio-quarter returns are calendar mark-to-market attribution.',
                         'Daily top20 outcomes overlap; independent-cycle basket results are reported separately.',
                         'Zero-cost re-simulation changes sizing and can change holdings; exact direct cost drag uses the fixed actual closed trades.',
                         'Allocation comparison shares top20 stocks and nonoverlapping schedule; comparison with the daily ten-position planner also changes timing.',
                         'Corporate-action gaps remain unknown. Incomplete books expose only closed-trade and marked prefix attribution.',
                         'Existing universe survivorship, adjusted prices, industry taxonomy and execution assumptions remain.']}
    atomic_json(report_file, report)
    print(f'COMPLETE {report_file}', flush=True)


if __name__ == '__main__':
    main()
