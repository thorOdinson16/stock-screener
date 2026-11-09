"""Frozen information ablation and separate prospective shadow trial."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.model_tournament import prepare, sha, evaluate, TECHNICAL_FEATURES, checked_manifest
from ml.evaluation.tournament_models import training_rows, date_weights
from ml.evaluation.information import (sector_features, attach_earnings, document_facts,
                                       EARNINGS_FEATURES, SECTOR_FEATURES, announcement_features)
from ml.evaluation.retail import Policy
from ml.evaluation.top20_experiment import candidate_gate, economic_score

ARMS = {'technical_control': TECHNICAL_FEATURES,
        'sector_candidate': TECHNICAL_FEATURES + SECTOR_FEATURES,
        'earnings_candidate': TECHNICAL_FEATURES + SECTOR_FEATURES + EARNINGS_FEATURES}


from ml.evaluation.information_model import InformationModel


def audit_archive(directory):
    accepted, rejected, sources = [], [], {}
    for path in sorted((directory / 'filings').glob('*.json')):
        record = json.loads(path.read_text())
        sources[str(path.resolve())] = sha(path)
        try:
            body = Path(record['document_file'])
            if sha(body) != record['document_sha256']:
                raise ValueError('Original document hash changed')
            sources[str(body)] = sha(body)
            facts = document_facts(record, body, record['first_seen_at'])
            accepted.append(facts)
        except Exception as e:
            rejected.append({'source': record['source'], 'reason': str(e)[:200]})
    # Same original filing may appear in the legacy index and current RSS.
    unique = {}
    for row in accepted:
        key = (row['symbol'], row['document_sha256'], row['published_at'])
        if key not in unique or row['first_seen_at'] < unique[key]['first_seen_at']:
            unique[key] = row
    # If the same URL/publication time is observed with revised bytes, that
    # version is available only after it was actually observed.
    versions = {}
    for row in unique.values():
        versions.setdefault((row['source'], row['published_at']), []).append(row)
    for group in versions.values():
        for row in sorted(group, key=lambda r: r['first_seen_at'])[1:]:
            from datetime import timedelta
            from ml.evaluation.information import timestamp
            observed_day = (timestamp(row['first_seen_at']).date() + timedelta(days=1)).isoformat()
            row['available_date'] = max(row['available_date'], observed_day)
    return list(unique.values()), rejected, sources


def write_frozen(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x') as f:
            json.dump(payload, f, allow_nan=False, indent=2)
    except FileExistsError:
        if json.loads(path.read_text()) != payload:
            raise RuntimeError(f'Frozen file changed: {path}')


def prospective_summary(paper, sessions):
    """Two feature candidates x three controls, prescheduled independent cohorts."""
    from random import Random
    items = []
    for path in sorted((paper / 'predictions').glob('*.json')):
        record = json.loads(path.read_text())
        file = paper / 'outcomes' / path.name
        outcome = json.loads(file.read_text()) if file.exists() else {}
        future = [i for i,d in enumerate(sessions) if d > record['publication_date']]
        items.append((record, outcome, future[0] if future else None))
    matured = [v for v in items if v[2] is not None and v[2] + 21 < len(sessions)]
    schedule, prior = [], -1
    for item in matured:
        if item[2] > prior:
            schedule.append(item)
            prior = item[2] + 21
    comparisons = {}
    for candidate in ('sector_candidate', 'earnings_candidate'):
        for control in ('technical_control','momentum','current_model'):
            pairs = []
            total_known = 0
            for _, outcome, _ in matured:
                a,b = outcome.get(candidate,{}),outcome.get(control,{})
                total_known += int(a.get('status') == b.get('status') == 'complete' and a.get('net_return') is not None and b.get('net_return') is not None)
            for _, outcome, _ in schedule:
                a,b = outcome.get(candidate,{}),outcome.get(control,{})
                if a.get('status') == b.get('status') == 'complete' and a.get('net_return') is not None and b.get('net_return') is not None:
                    pairs.append(a['net_return']-b['net_return'])
            interval = None
            if len(pairs) >= 2:
                rng = Random(42)
                draws = sorted(sum(rng.choice(pairs) for _ in pairs)/len(pairs) for _ in range(5000))
                tail = .05 / 12  # six prespecified candidate/control comparisons
                interval = [draws[int(tail*len(draws))],draws[min(len(draws)-1,int((1-tail)*len(draws)))]]
            coverage = total_known/len(matured) if matured else None
            comparisons[candidate+'/'+control] = {'complete_independent_pairs':len(pairs), 'coverage':coverage,
                'mean_difference':float(np.mean(pairs)) if pairs else None, 'familywise_95pct_interval':interval,
                'supported':len(pairs)>=8 and coverage is not None and coverage>=.98 and interval is not None and interval[0]>0}
    return {'captured_cohorts':len(items),'matured_cohorts':len(matured),'scheduled_independent_cohorts':len(schedule),
            'status':'awaiting_mature_cohorts' if not matured else 'descriptive_paper_evidence',
            'comparisons':comparisons,'production_ready':False}


def capture_paper(research, fresh, reference_path):
    import joblib
    from ml.evaluation.tournament_forward import capture, freshness
    from ml.evaluation.paper_portfolio import evaluate_book
    from ml.evaluation.compare_baselines import candidates
    from ml.evaluation.corporate_actions import load_events
    protocol = json.loads((research / 'protocol.json').read_text())
    frozen = json.loads((research / 'paper_models.json').read_text())
    for name, expected in protocol['source_hashes'].items():
        if sha(ROOT / 'ml/evaluation' / name) != expected:
            raise RuntimeError('Information trial source changed; version the trial')
    if sha(research / 'protocol.json') != frozen['protocol_sha256']:
        raise RuntimeError('Frozen trial protocol changed')
    manifest = checked_manifest(fresh)
    now = datetime.now(IST)
    paper = research / 'paper'
    paper.mkdir(exist_ok=True)
    if not freshness(manifest['last_session'],now.date().isoformat()):
        atomic_json(paper / 'status.json',{'status':'waiting_for_fresh_prices','production_changed':False})
        return
    reference = json.loads(reference_path.read_text())
    if reference['input_manifest'] != sha(fresh / 'manifest.json') or reference['trade_date'] != manifest['last_session']:
        raise RuntimeError('Current-model control does not match fresh prices')
    # Documents observed after protocol freeze can be used in future cohorts,
    # while model features/weights/rules stay fixed. Keep full per-cohort hashes.
    archive = Path(protocol['archive_directory'])
    facts,rejected,source_hashes = audit_archive(archive)
    frame = pd.read_parquet(fresh / 'dataset.parquet').replace([np.inf,-np.inf],np.nan)
    frame = frame[~frame.symbol.str.startswith('^')].dropna(subset=TECHNICAL_FEATURES)
    raw = pd.read_parquet(fresh / 'bars.parquet')
    bars = {(r.symbol,r.trade_date):{k:getattr(r,k) if pd.notna(getattr(r,k)) else None for k in ('open','low','close','volume')} for r in raw.itertuples()}
    sessions = sorted(raw[raw.symbol=='^NSEI'].trade_date.unique())
    enriched = sector_features(frame,bars,sessions)
    signal = manifest['last_session']
    latest = enriched[enriched.trade_date==signal].copy()
    # Price and information timestamps are explicit. Info known at actual capture
    # can enter only AFTER publication, never as a backdated Nov-6 prediction.
    latest = attach_earnings(latest,facts,observed_cutoff=now.isoformat(),feature_day=now.date().isoformat())
    for name,entry in frozen['models'].items():
        file = research / entry['file']
        if sha(file) != entry['sha256']:
            raise RuntimeError('Frozen information weights changed')
        latest[name] = joblib.load(file).predict(latest)
    latest['current_model'] = latest.symbol.map({r['symbol']:r['score'] for r in reference['rows']})
    latest = latest.dropna(subset=['current_model'])
    context = pd.read_parquet(fresh / 'context.parquet').set_index(['symbol','trade_date'])
    positions = {d:i for i,d in enumerate(sessions)}
    stats = {}
    for row in latest.itertuples():
        day = positions[signal]
        prior = bars.get((row.symbol,sessions[day-63]),{}).get('close') if day>=63 else None
        close = bars[(row.symbol,signal)]['close']
        c = context.loc[(row.symbol,signal)]
        stats[(row.symbol,signal)] = {'symbol':row.symbol,'trade_date':signal,'close':close,'industry':row.industry,
            'momentum':close/prior-1 if prior and close else None,'volume_avg_20d':c.volume_avg_20d,'volatility_20d':c.volatility_20d}
    records = latest[['symbol','technical_control','sector_candidate','earnings_candidate','current_model']].copy()
    records['score'] = records.technical_control
    events = load_events()
    pool = candidates(records.to_dict('records'),stats,signal,Policy(),events)
    baskets = {}
    for name in ('technical_control','sector_candidate','earnings_candidate','current_model','momentum'):
        chosen = sorted(pool,key=lambda r:(-r[name],r['symbol']))[:20]
        baskets[name] = [{**{k:r.get(k) for k in ('symbol','industry','trade_date','volume_avg_20d','volatility_20d','momentum')},'score':r[name]} for r in chosen]
    trial_hash = frozen['trial_sha256']
    coverage = {k:int(latest[k].notna().sum()) for k in EARNINGS_FEATURES+SECTOR_FEATURES}
    annotations = latest[['symbol']+EARNINGS_FEATURES+SECTOR_FEATURES].replace([np.inf,-np.inf],np.nan)
    # Announcements remain observation-only until continuous archive coverage and
    # matured training history exist. Missing counts are never represented as zero.
    stamp = now.date().isoformat()
    path = paper / 'features' / (stamp+'.json')
    write_frozen(path,{'captured_at':now.isoformat(),'price_session':signal,'rows':json.loads(annotations.to_json(orient='records'))}) if not path.exists() else None
    status = capture(paper/'predictions',stamp,signal,trial_hash,baskets,
        {'trial':'prespecified information ablations, research controls; not a promoted historical winner',
         'price_session':signal,'information_asof':now.isoformat(),'paired_pool_size':len(pool),
         'source_manifest':sha(fresh/'manifest.json'),'reference_sha256':sha(reference_path),
         'information_files':source_hashes,'feature_coverage':coverage,'features_file_sha256':sha(path),
         'announcements':'collection only; no verified continuous observation window or matured training history'})
    for file in sorted((paper/'predictions').glob('*.json')):
        record = json.loads(file.read_text())
        books = {}
        for name,rows in record['baskets'].items():
            receipt_path = paper/'entries'/f'{file.stem}_{name}.json'
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            result = evaluate_book(record,rows,bars,sessions,events,receipt)
            if receipt is None and result.get('entry_receipt'):
                write_frozen(receipt_path,result['entry_receipt'])
            books[name] = result
        atomic_json(paper/'outcomes'/file.name,books)
    assessment = prospective_summary(paper,sessions)
    atomic_json(paper/'assessment.json',assessment)
    atomic_json(paper/'status.json',{**status,**assessment,'paired_pool_size':len(pool),'feature_coverage':coverage,'production_changed':False})
    print(json.dumps({'paper':status,'pool':len(pool),'matured':assessment['matured_cohorts']}),flush=True)


def main():
    import joblib
    from ml.evaluation.corporate_actions import load_events
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,default=ROOT/'ml/evaluation/results/information_archive')
    parser.add_argument('--base',type=Path,default=ROOT/'ml/evaluation/results/model_tournament_20261108')
    parser.add_argument('--output',type=Path,default=ROOT/'ml/evaluation/results/information_research_20261109')
    parser.add_argument('--fresh',type=Path)
    parser.add_argument('--reference',type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if not (args.output/'report.json').exists():
        facts,rejected,files = audit_archive(args.archive)
        if not facts:
            raise RuntimeError('No verified information records available')
        base_protocol = json.loads((args.base/'protocol.json').read_text())
        inputs = Path(base_protocol['input_directory'])
        reference = Path(base_protocol['reference_directory'])
        sources = ('information.py','information_model.py','information_research.py','model_tournament.py','tournament_models.py','retail.py',
                   'compare_baselines.py','forecast_outcomes.py','top20_experiment.py','paper_portfolio.py','tournament_forward.py')
        protocol = {'archive_directory':str(args.archive.resolve()),'input_manifest':sha(inputs/'manifest.json'),
            'base_protocol':sha(args.base/'protocol.json'),'filing_files':files,
            'source_hashes':{n:sha(ROOT/'ml/evaluation'/n) for n in sources},'arms':ARMS,
            'model':'Fixed LightGBM squared-error regressor, 7 leaves, 100 trees, lr=.04, child>=100, L2=10, seed=42; train-only median imputer plus missing indicators',
            'target':'21-session fixed-policy net return; no hyperparameter search',
            'financial_availability':'Publication + next calendar day; only as-filed consolidated quarterly INR contexts; prior-quarter/year comparisons from filings public by feature date, max age180d; actual first-seen required live',
            'sector':'Six leave-one-out breadth/relative-strength/dispersion/volume features, >=5 other stocks, common-calendar raw windows, no fills',
            'announcements':'Versioned raw observation ledger only; excluded from models until continuous coverage and matured training history support a verified experiment',
            'selection':'Existing positive top20 economics and stable fold advantage plus three-scenario retail gate; no promotion',
            'paper':'All three prespecified ablations recorded as research controls regardless of historical winner; same paired pool and current-model/momentum controls',
            'paper_assessment':{'minimum_nonoverlapping_cycles':8,'minimum_pair_coverage':.98,'comparisons':6,'bootstrap_draws':5000,'seed':42,'familywise_alpha':.05},
            'policy':asdict(Policy())}
        path = args.output/'protocol.json'
        if path.exists() and json.loads(path.read_text()) != protocol:
            raise RuntimeError('Information protocol changed; choose new output')
        atomic_json(path,protocol)
        print('INFORMATION protocol frozen',flush=True)
        atomic_json(args.output/'filing_audit.json',{'accepted':facts,'rejected':rejected,'symbols':len({f['symbol'] for f in facts})})
        frame,bars,sessions,statistics,_ = prepare(inputs,args.base)
        enriched = attach_earnings(sector_features(frame,bars,sessions),facts)
        features_audit = {c:{'known_rows':int(enriched[c].notna().sum()),'known_dates':int(enriched.loc[enriched[c].notna(),'trade_date'].nunique()),
                              'symbols':int(enriched.loc[enriched[c].notna(),'symbol'].nunique())} for c in EARNINGS_FEATURES+SECTOR_FEATURES}
        folds,refs = {},[]
        for f in range(1,6):
            file = reference/f'rank21_fold{f}.csv'
            if sha(file) != base_protocol['reference_files'][file.name]:
                raise RuntimeError('Original controls changed')
            p = pd.read_csv(file);p['fold']=f;refs.append(p)
            days = sorted(p.trade_date.unique())
            folds[f] = {'days':days,'train':sessions[:sessions.index(days[0])-63]}
        ref = pd.concat(refs,ignore_index=True)
        fold_by_date = {d:f for f,spec in folds.items() for d in spec['days']}
        periods = {'validation':(folds[1]['days'][0],folds[3]['days'][-1]),'assessment':(folds[4]['days'][0],sessions[-1])}
        checkpoints = args.output/'predictions';checkpoints.mkdir(exist_ok=True)
        training_coverage = {}
        def fit_fold(arm,f):
            file = checkpoints/f'{arm}_fold{f}.parquet'
            receipt = file.with_suffix('.json')
            train = training_rows(enriched,'execution',folds[f]['train'],folds[f]['days'][0])
            coverage = {c:int(train[c].notna().sum()) for c in EARNINGS_FEATURES}
            training_coverage[f'{arm}_fold{f}'] = coverage
            if file.exists() and receipt.exists():
                if sha(file) != json.loads(receipt.read_text())['sha256']:raise RuntimeError('Checkpoint changed')
                return pd.read_parquet(file)
            test = enriched.merge(ref.loc[ref.fold==f,['symbol','trade_date','fold']],validate='one_to_one')
            print(f'FIT INFORMATION {arm} fold{f}',flush=True)
            model = InformationModel(ARMS[arm]).fit(train)
            scores = model.predict(test)
            p = test[['symbol','trade_date','fold']].copy();p['score']=scores
            p.to_parquet(file,index=False)
            atomic_json(receipt,{'sha256':sha(file),'train_latest_maturity':train.scheduled_exit_date.max(),
                                'test_start':folds[f]['days'][0],'train_rows':len(train),'test_rows':len(test),'financial_training_coverage':coverage})
            return p
        events = load_events()
        predictions,results,reasons = {},{},{}
        for arm in ARMS:
            p = pd.concat([fit_fold(arm,f) for f in (1,2,3)],ignore_index=True)
            predictions[arm]=p
            result=evaluate(p,statistics,bars,sessions,events,*periods['validation'],args.output/f'{arm}_validation.json')
            results[arm]={'validation':result}
            if arm!='technical_control':
                reasons[arm],_ = candidate_gate(result,fold_by_date)
                if arm=='earnings_candidate' and sum(training_coverage[f'{arm}_fold{f}']['info_revenue_qoq']>=1000 for f in (1,2,3))<2:
                    reasons[arm].append('Earnings-change training coverage below1000 rows in at least two validation folds')
        qualified=[n for n in reasons if not reasons[n]]
        winner=max(qualified,key=lambda n:economic_score(results[n]['validation'])) if qualified else None
        atomic_json(args.output/'selection.json',{'selected_on_validation':winner,'rejection_reasons':reasons,'assessment_used':False})
        print(f'INFORMATION selection locked: {winner}',flush=True)
        for arm in ARMS:
            p=pd.concat([fit_fold(arm,f) for f in (4,5)],ignore_index=True)
            p.to_parquet(args.output/f'{arm}_assessment_signals.parquet',index=False)
            results[arm]['assessment']=evaluate(p,statistics,bars,sessions,events,*periods['assessment'],args.output/f'{arm}_assessment.json')
        # Freeze exploratory prospective ablations, not a claimed historical winner.
        train=training_rows(enriched,'execution',sessions,sessions[-1])
        models={}
        for arm in ARMS:
            file=args.output/f'{arm}.joblib'
            if file.exists():raise RuntimeError('Frozen model already exists without final receipt; use new output')
            joblib.dump(InformationModel(ARMS[arm]).fit(train),file)
            models[arm]={'file':file.name,'sha256':sha(file)}
        trial_hash=__import__('hashlib').sha256(json.dumps(models,sort_keys=True).encode()).hexdigest()
        atomic_json(args.output/'paper_models.json',{'models':models,'trial_sha256':trial_hash,'protocol_sha256':sha(path),
                    'trained_latest_maturity':train.scheduled_exit_date.max(),'trained_before':sessions[-1],
                    'role':'Prespecified exploratory ablations; no production or historical-winner promotion'})
        report={'status':'historical_research','production_changed':False,'promotion_allowed':False,'protocol':protocol,
                'periods':periods,'selected_on_validation':winner,'rejection_reasons':reasons,'results':results,
                'feature_coverage':features_audit,'training_coverage':training_coverage,
                'filing_coverage':{'accepted':len(facts),'rejected':len(rejected),'symbols':len({r['symbol'] for r in facts})},
                'limits':['Historical periods already researched; partial financial corpus and current-universe/industry taxonomy.',
                          'Verified missing values remain missing; imputation fits training only. Announcement features excluded from predictive claims.',
                          'Prospective models are prespecified research controls, not endorsed candidates; outcomes must occur in future.',
                          'Provider-adjusted prices, simplified execution and unknown corporate-action entitlements remain.']}
        atomic_json(args.output/'report.json',report)
        print('INFORMATION research complete',flush=True)
    if args.fresh and args.reference:
        capture_paper(args.output,args.fresh,args.reference)


if __name__=='__main__':main()
