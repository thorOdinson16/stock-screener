"""Fixed target-resolution x selection-rule experiment on frozen history.

No architecture search, production training or retrospective promotion.
"""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import prepare, sha, evaluate, quality, TECHNICAL_FEATURES
from ml.evaluation.tournament_models import training_rows, date_weights
from ml.evaluation.xgb_kalman import fit_xgb, GRID, paired_check
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.corporate_actions import load_events
from ml.evaluation.retail import Policy

ARMS = ('coarse_rank', 'fine_rank', 'net_regression')
RULES = ('ic', 'top20_net')


def fine_labels(train):
    """One ordinal relevance per distinct outcome within date; preserve ties."""
    return (train.groupby('trade_date').net_return.rank(method='dense') - 1).to_numpy(dtype=np.int32)


def fit_target(train, features, arm, params):
    train = train.sort_values(['trade_date', 'symbol'])
    if arm == 'coarse_rank':
        return fit_xgb(train, features, 'execution', params)
    from xgboost import XGBRanker, XGBRegressor
    x = train[features].to_numpy(dtype=np.float32)
    y = train.net_return.to_numpy(dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Training features and outcomes must be finite')
    fixed = dict(learning_rate=.04, min_child_weight=100., reg_lambda=10., tree_method='hist',
                 device='cpu', n_jobs=4, random_state=42, subsample=1., colsample_bytree=1., **params)
    if arm == 'fine_rank':
        sizes = train.groupby('trade_date', sort=False).size().to_numpy()
        model = XGBRanker(objective='rank:ndcg', ndcg_exp_gain=False,
                          lambdarank_pair_method='topk', lambdarank_num_pair_per_sample=20, **fixed)
        model.fit(x, fine_labels(train), group=sizes, sample_weight=np.ones(len(sizes)), verbose=False)
    elif arm == 'net_regression':
        model = XGBRegressor(objective='reg:squarederror', **fixed)
        # Raw realized net return, no clipping or fit to validation outcomes.
        model.fit(x, y, sample_weight=date_weights(train), verbose=False)
    else:
        raise ValueError(f'Unknown arm: {arm}')
    return model


def economic_score(result):
    """Unknown selected outcomes invalidate the configuration score."""
    strategies = result['selection_diagnostic']['strategies']
    a, b = strategies['model'], strategies['momentum']
    if a['status'] != 'complete' or b['status'] != 'complete' or len(a['cycles']) != len(b['cycles']):
        return None
    differences = []
    for x, y in zip(a['cycles'], b['cycles']):
        if x['signal_date'] != y['signal_date']:
            raise ValueError('Unpaired cycle schedule')
        if x['status'] != 'complete' or y['status'] != 'complete':
            return None
        differences.append(x['net_return'] - y['net_return'])
    return float(np.mean(differences)) if differences else None


def choose_config(trials, rule):
    field = 'mean_daily_rank_ic' if rule == 'ic' else 'mean_cycle_excess'
    values = [t[field] for t in trials]
    valid = [i for i, value in enumerate(values) if value is not None and np.isfinite(value)]
    return max(valid, key=lambda i: (values[i], -i)) if valid else None


def candidate_gate(result, fold_by_date):
    reasons = []
    score = economic_score(result)
    selection = result['selection_diagnostic']['strategies']['model']
    if score is None:
        reasons.append('Incomplete equal-notional comparison')
    elif score <= 0 or selection['net_return'] <= 0:
        reasons.append('No positive net selection advantage over momentum')
    by_fold = {}
    a = selection['cycles']
    b = result['selection_diagnostic']['strategies']['momentum']['cycles']
    for x, y in zip(a, b):
        if x['signal_date'] != y['signal_date']:
            raise ValueError('Unpaired cycle schedule')
        if x['status'] == y['status'] == 'complete':
            by_fold.setdefault(fold_by_date[x['signal_date']], []).append(x['net_return'] - y['net_return'])
    positive = sum(len(v) >= 3 and np.mean(v) > 0 for v in by_fold.values())
    if positive < 2:
        reasons.append('Positive cycle advantage in fewer than two validation folds with >=3 cycles each')
    for scenario in ('base', 'double_costs', 'entry_delayed'):
        x, y = result['retail'][scenario], result['matched_momentum_retail'][scenario]
        if x['status'] != 'complete' or y['status'] != 'complete':
            reasons.append(f'Incomplete {scenario} portfolio')
        elif x['net_return'] <= max(0., y['net_return']):
            reasons.append(f'No positive retail advantage over momentum under {scenario}')
    base = result['retail']['base']
    if base['closed_trades'] < 20 or base['max_drawdown'] is None or base['max_drawdown'] < -.25:
        reasons.append('Trade-count or drawdown gate failed')
    return reasons, {str(k): {'cycles': len(v), 'mean_cycle_excess': float(np.mean(v))} for k, v in by_fold.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research', type=Path, default=ROOT / 'ml/evaluation/results/xgb_kalman_20261109')
    parser.add_argument('--output', type=Path, default=ROOT / 'ml/evaluation/results/top20_targets_20261109')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / 'report.json').exists():
        print('Completed experiment is frozen; choose a new output')
        return
    old = json.loads((args.research / 'report.json').read_text())
    old_protocol = old['protocol']
    base = Path(old_protocol['base_directory'])
    inputs = Path(old_protocol['input_directory'])
    for name, digest in old_protocol['source_hashes'].items():
        if sha(ROOT / 'ml/evaluation' / name) != digest:
            raise RuntimeError(f'Original research source changed: {name}')
    if sha(inputs / 'manifest.json') != old_protocol['input_manifest'] or sha(base / 'protocol.json') != old_protocol['base_protocol']:
        raise RuntimeError('Original snapshot or base protocol changed')
    if json.loads((args.research / 'protocol.json').read_text()) != old_protocol:
        raise RuntimeError('Original research protocol differs')
    reference = Path(json.loads((base / 'protocol.json').read_text())['reference_directory'])
    controls = [args.research / 'xgb_execution_assessment_signals.parquet']
    for config in range(len(GRID)):
        for f in (1, 2, 3):
            controls += [args.research / 'predictions' / f'xgb_execution_config{config}_fold{f}{ext}' for ext in ('.json', '.parquet')]
    source_names = ('top20_experiment.py', 'xgb_kalman.py', 'model_tournament.py', 'tournament_models.py',
                    'retail.py', 'compare_baselines.py', 'forecast_outcomes.py', 'corporate_actions.py')
    protocol = {'research_directory': str(args.research.resolve()), 'research_report': sha(args.research / 'report.json'),
                'input_manifest': sha(inputs / 'manifest.json'),
                'reference_files': {n: sha(reference / n) for n in old_protocol['reference_files']},
                'control_files': {str(p.resolve()): sha(p) for p in controls},
                'source_hashes': {n: sha(ROOT / 'ml/evaluation' / n) for n in source_names},
                'versions': {n: importlib.metadata.version(n) for n in ('numpy', 'pandas', 'xgboost-cpu')},
                'arms': list(ARMS), 'rules': list(RULES), 'grid': GRID,
                'fixed': {'learning_rate': .04, 'min_child_weight': 100., 'reg_lambda': 10., 'seed': 42,
                          'rank_objective': 'rank:ndcg', 'linear_gain': True, 'pair_topk': 20},
                'features': TECHNICAL_FEATURES, 'policy': asdict(Policy()), 'target': '21-session fixed-policy net return',
                'train': 'Original expanding folds; 63-session embargo and strictly matured scheduled outcomes; no early stopping',
                'sample': 'Original XGBoost/Kalman common inference keys, shared across all arms and rules; no outcome filtering',
                'config_selection': 'IC versus mean paired independent-cycle top20 net excess over matched momentum; lower grid index breaks ties; incomplete economics cannot win',
                'candidate_selection': 'Only top20_net variants; positive equal-notional return and excess, >=2 validation folds with >=3 cycles and positive excess, positive actual retail outperformance in base/double-cost/delayed scenarios, >=20 trades, DD>=-25%; highest validation mean cycle excess wins',
                'hypotheses': 5, 'assessment': 'Existing later history, descriptive familywise bootstrap across five new variants; no production promotion'}
    protocol_path = args.output / 'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != protocol:
        raise RuntimeError('Protocol changed; use a new output')
    atomic_json(protocol_path, protocol)
    print('TOP20 protocol frozen', flush=True)
    frame, bars, sessions, statistics, _ = prepare(inputs, base)
    events = load_events()
    # Existing keys were set using past-only availability. No label-based intersection.
    key_parts = []
    for f in (1, 2, 3):
        key_parts.append(pd.read_parquet(args.research / 'predictions' / f'xgb_execution_config0_fold{f}.parquet')[['symbol', 'trade_date', 'fold']])
    key_parts.append(pd.read_parquet(controls[0])[['symbol', 'trade_date', 'fold']])
    keys = pd.concat(key_parts, ignore_index=True)
    if keys.duplicated(['symbol', 'trade_date']).any() or keys.fold.nunique() != 5:
        raise RuntimeError('Invalid frozen common keys')
    folds = {}
    for f in range(1, 6):
        path = reference / f'rank21_fold{f}.csv'
        if sha(path) != old_protocol['reference_files'][path.name]:
            raise RuntimeError('Original reference changed')
        days = sorted(pd.read_csv(path).trade_date.unique())
        folds[f] = {'days': days, 'train_dates': sessions[:sessions.index(days[0]) - 63]}
    fold_by_date = {d: f for f, spec in folds.items() for d in spec['days']}
    periods = old['periods']
    checkpoints = args.output / 'predictions'
    checkpoints.mkdir(exist_ok=True)

    def fit_fold(arm, config, f):
        path = checkpoints / f'{arm}_config{config}_fold{f}.parquet'
        receipt = path.with_suffix('.json')
        if path.exists() and receipt.exists():
            if json.loads(receipt.read_text())['sha256'] != sha(path):
                raise RuntimeError('Checkpoint changed')
            return pd.read_parquet(path)
        if arm == 'coarse_rank':
            cached = args.research / 'predictions' / f'xgb_execution_config{config}_fold{f}.parquet'
            if cached.exists():
                saved = json.loads(cached.with_suffix('.json').read_text())
                if saved['sha256'] != sha(cached) or saved['train_latest_maturity'] >= saved['test_start']:
                    raise RuntimeError('Invalid original model receipt')
                p = pd.read_parquet(cached)
                p.to_parquet(path, index=False)
                atomic_json(receipt, {**saved, 'sha256': sha(path), 'reused_from': str(cached.resolve())})
                return p
        spec = folds[f]
        train = training_rows(frame, 'execution', spec['train_dates'], spec['days'][0])
        test = frame.merge(keys[keys.fold == f], on=['symbol', 'trade_date'], validate='one_to_one')
        print(f'FIT {arm} config{config} fold{f}: {len(train)} / {len(test)}', flush=True)
        started = time.monotonic()
        model = fit_target(train, TECHNICAL_FEATURES, arm, GRID[config])
        scores = model.predict(test[TECHNICAL_FEATURES].to_numpy(dtype=np.float32))
        if not np.isfinite(scores).all():
            raise RuntimeError('Nonfinite predictions')
        p = test[['symbol', 'trade_date', 'fold']].copy()
        p['score'] = scores
        p.to_parquet(path, index=False)
        atomic_json(receipt, {'sha256': sha(path), 'train_rows': len(train), 'test_rows': len(test),
                             'train_latest_maturity': train.scheduled_exit_date.max(), 'test_start': spec['days'][0],
                             'seconds': time.monotonic() - started})
        return p

    trials, predictions, evaluations = {}, {}, {}
    for arm in ARMS:
        trials[arm] = []
        for config in range(len(GRID)):
            p = pd.concat([fit_fold(arm, config, f) for f in (1, 2, 3)], ignore_index=True)
            predictions[(arm, config)] = p
            print(f'VALIDATION {arm} config{config}', flush=True)
            result = evaluate(p, statistics, bars, sessions, events, *periods['validation'], args.output / f'{arm}_config{config}_validation.json')
            evaluations[(arm, config)] = result
            q = quality(p, frame, 'execution', periods['validation'][1])
            trials[arm].append({'params': GRID[config], 'mean_daily_rank_ic': q['mean_daily_rank_ic'],
                                'mean_cycle_excess': economic_score(result), 'validation': result})
    selected, rejection, candidates, fold_consistency = {}, {}, {}, {}
    for arm in ARMS:
        for rule in RULES:
            name = f'{arm}_{rule}'
            config = choose_config(trials[arm], rule)
            selected[name] = config
            if config is not None and rule == 'top20_net':
                result = evaluations[(arm, config)]
                reasons, stability = candidate_gate(result, fold_by_date)
                rejection[name], fold_consistency[name] = reasons, stability
                if not reasons:
                    candidates[name] = economic_score(result)
    winner = max(candidates, key=lambda n: (candidates[n], n)) if candidates else None
    selection = {'configs': selected, 'selected_on_validation': winner, 'rejection_reasons': rejection,
                 'fold_consistency': fold_consistency, 'trials': trials, 'assessment_used': False}
    selection_file = args.output / 'selection.json'
    if selection_file.exists() and json.loads(selection_file.read_text()) != selection:
        raise RuntimeError('Validation choice changed on resume')
    atomic_json(selection_file, selection)
    print(f'SELECTION locked: {winner}', flush=True)
    results, checks = {}, {}
    later_evaluations = {}
    for name, config in selected.items():
        if config is None:
            results[name] = {'status': 'no_valid_validation_config'}
            continue
        arm = next(a for a in ARMS if name.startswith(a + '_'))
        key = (arm, config)
        if key not in later_evaluations:
            p = pd.concat([fit_fold(arm, config, f) for f in (4, 5)], ignore_index=True)
            p.to_parquet(args.output / f'{arm}_config{config}_assessment_signals.parquet', index=False)
            print(f'ASSESSMENT {arm} config{config}', flush=True)
            later_evaluations[key] = evaluate(p, statistics, bars, sessions, events, *periods['assessment'], args.output / f'{arm}_config{config}_assessment.json')
        result = later_evaluations[key]
        results[name] = {'config': config, 'params': GRID[config], 'validation': evaluations[key], 'assessment': result}
        checks[name] = paired_check(result, family_size=5)
        # Helper's generic family-size calculation is reused; label it accurately.
        checks[name]['reasons'] = [r.replace('three-hypothesis', 'five-hypothesis') for r in checks[name]['reasons']]
    # Current production-model reference remains a frozen comparison, never refitted.
    results['current_model_reference'] = old['results']['current_model_reference']
    report = {'status': 'historical_research', 'production_changed': False, 'promotion_allowed': False,
              'protocol': protocol, 'periods': periods, 'coverage': old['coverage'],
              'selected_on_validation': winner, 'configs': selected, 'rejection_reasons': rejection,
              'fold_consistency': fold_consistency, 'results': results, 'assessment_checks': checks,
              'limits': ['Already examined historical periods, not new holdout or prospective evidence.',
                         'Three target arms x two configuration-selection rules; only two configurations each. Metric changes and past experiments add selection bias.',
                         'Fine ranks retain ties and reward ordinal relevance, not actual monetary gaps; raw-return regression can emphasize outliers.',
                         'Adjusted prices, current-universe/industry taxonomy and unresolved corporate entitlements remain.',
                         'Bootstrap adjustment covers five new variants only, not all historical project-wide testing.',
                         'No production model, trading policy, existing frozen paper candidate or live confidence was changed.']}
    atomic_json(args.output / 'report.json', report)
    pct = lambda v: 'unknown' if v is None else f'{100*v:.2f}%'
    lines = ['# Top-20 targets and selection — historical research', '', f'Validation choice: {winner or "none qualified"}; no promotion.', '',
             '| Arm / selection rule | Config | Validation retail | Later retail | Matched momentum | Later equal-notional | Double costs | Delayed entry |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, r in results.items():
        if 'assessment' not in r:
            continue
        a, v = r['assessment'], r['validation']
        lines.append(f"| {name} | {r.get('config', 'reference')} | {pct(v['retail']['base']['net_return'])} | {pct(a['retail']['base']['net_return'])} | {pct(a['matched_momentum_retail']['base']['net_return'])} | {pct(a['selection_diagnostic']['strategies']['model']['net_return'])} | {pct(a['retail']['double_costs']['net_return'])} | {pct(a['retail']['entry_delayed']['net_return'])} |")
    lines += [''] + [f'- {s}' for s in report['limits']]
    (args.output / 'summary.md').write_text('\n'.join(lines) + '\n')
    print(f'COMPLETE {args.output / "summary.md"}', flush=True)


if __name__ == '__main__':
    main()
