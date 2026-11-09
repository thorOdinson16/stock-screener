"""Sector-relative targets x abstention x horizon, fixed before fitting.

Historical research on already examined periods. No live or paper-model writes.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import prepare,sha
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.pilot_coverage import require_ready,CONFIG
from ml.evaluation.pilot_experiment import ARMS
from ml.evaluation.information_model import InformationModel
from ml.evaluation.corporate_actions import load_events,fingerprint
from ml.evaluation.retail import Policy
from ml.evaluation.decision_targets import sector_forward_returns,execution_targets,matured_training
from ml.evaluation.decision_evaluation import evaluate,qualify,SCENARIOS

DEFAULT=ROOT/'ml/evaluation/results/decision_experiment_20261109_v3'


def protected_files():
    trial=ROOT/'ml/evaluation/results/information_research_20261109'
    paths=[trial/'protocol.json',trial/'paper_models.json',*trial.glob('*.joblib'),
           *(trial/'paper/predictions').glob('*.json'),*(trial/'paper/features').glob('*.json'),
           *(ROOT/'ml/models').glob('**/*')]
    return {str(p.resolve()):sha(p) for p in paths if p.is_file()}


def verify_inputs(protocol,output):
    for name,expected in protocol['source_hashes'].items():
        if sha(ROOT/name)!=expected:raise RuntimeError('Research source changed; version the experiment: '+name)
    for path,expected in protocol['protected_files'].items():
        if sha(path)!=expected:raise RuntimeError('Protected production/paper evidence changed: '+path)
    for path,expected in protocol['input_hashes'].items():
        if sha(path)!=expected:raise RuntimeError('Research input changed: '+path)
    if (output/'report.json').exists():
        report=json.loads((output/'report.json').read_text())
        if report['protocol_sha256']!=sha(output/'protocol.json'):raise RuntimeError('Report protocol changed')
        for path,expected in report['artifacts'].items():
            if sha(output/path)!=expected:raise RuntimeError('Completed research artifact changed: '+path)


def quality(scores,data,end):
    merged=scores.merge(data[['symbol','trade_date','execution_net','sector_relative_net','target_maturity']],on=['symbol','trade_date'],validate='one_to_one')
    result={}
    for label in ('execution_net','sector_relative_net'):
        known=merged[label].notna()&merged.target_maturity.notna()&(merged.target_maturity<=end)
        values=[]
        for _,group in merged[known].groupby('trade_date'):
            if len(group)>=5 and group.score.nunique()>1 and group[label].nunique()>1:
                v=group.score.corr(group[label],method='spearman')
                if np.isfinite(v):values.append(float(v))
        result[label]={'known_rows':int(known.sum()),'evaluated_dates':len(values),'mean_daily_rank_ic':float(np.mean(values)) if values else None}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args();output=args.output;output.mkdir(parents=True,exist_ok=True)
    pilot=ROOT/'ml/evaluation/results/financial_pilot_v1';require_ready(pilot)
    previous=ROOT/'ml/evaluation/results/verified_pilot_experiment_20261109'
    old=json.loads((previous/'report.json').read_text())
    for name,expected in old['protocol']['source_hashes'].items():
        if sha(ROOT/'ml/evaluation'/name)!=expected:raise RuntimeError('Prior research definitions changed')
    for name,expected in old['artifacts'].items():
        if sha(previous/name)!=expected:raise RuntimeError('Prior pilot feature/prediction evidence changed')
    source_audit=output/'source_audit/report.json'
    if not source_audit.exists():raise RuntimeError('Run poller/expectations_sources.py first')
    audit=json.loads(source_audit.read_text())
    if audit['surprise_backtest_run'] or audit['consensus_records']!=0:raise RuntimeError('Unexpected source audit; version the experiment')
    base=ROOT/'ml/evaluation/results/model_tournament_20261108';base_protocol=json.loads((base/'protocol.json').read_text())
    inputs=Path(base_protocol['input_directory']);reference=Path(base_protocol['reference_directory'])
    refs=[];folds={};hashes={str(source_audit.resolve()):sha(source_audit),str(previous/'features.parquet'):sha(previous/'features.parquet'),
        str(pilot/'coverage_report.json'):sha(pilot/'coverage_report.json'),str(CONFIG):sha(CONFIG),str(base/'protocol.json'):sha(base/'protocol.json')}
    for source in audit['sources']:
        if source['status']=='retrieved':hashes[source['file']]=source['sha256']
    for f in range(1,6):
        path=reference/f'rank21_fold{f}.csv'
        if sha(path)!=base_protocol['reference_files'][path.name]:raise RuntimeError('Existing model reference changed')
        hashes[str(path)]=sha(path);p=pd.read_csv(path);p['fold']=f;refs.append(p)
        folds[f]={'start':p.trade_date.min(),'end':p.trade_date.max()}
    for name,expected in json.loads((inputs/'manifest.json').read_text())['files'].items():hashes[str(inputs/name)]=expected
    hashes[str(inputs/'manifest.json')]=sha(inputs/'manifest.json')
    sources=['decision_experiment.py','decision_targets.py','decision_evaluation.py','information_model.py','tournament_models.py','information.py',
        'model_tournament.py','pilot_experiment.py','pilot_coverage.py','retail.py','compare_baselines.py','forecast_outcomes.py','corporate_actions.py']
    protocol={'version':1,'research_scope':'Historical exploratory research on previously examined dates; not a new holdout or prospective validation',
        'input_hashes':hashes,'source_hashes':{'ml/evaluation/'+n:sha(ROOT/'ml/evaluation'/n) for n in sources},
        'protected_files':protected_files(),'corporate_actions_sha256':fingerprint(load_events()),'folds':{str(k):v for k,v in folds.items()},
        'validation_folds':[1,2,3],'assessment_folds':[4,5],'horizons':[21,63],'embargo_sessions':63,
        'features':ARMS['financial'],'model':'Existing fixed7-leaf100-tree InformationModel; no architecture/hyperparameter search; train-only imputation',
        'targets':{'absolute':'Fixed-policy next-close stock net return after stop/costs',
            'relative':'Same stock execution net return minus gross passive leave-one-out equal-weight industry return over the SAME scheduled next-close holding interval; long-only execution, no sector hedge'},
        'peer_rules':{'minimum_other_stocks':5,'minimum_known_fraction':.95,'membership':'Original tournament current-universe industry map; peers require63 consecutive valid closes THROUGH signal date. Denominator uses ONLY signal-time information; future missing outcomes do not shrink it'},
        'training':'Same eligible matured stock/date rows for both targets, including valid sector benchmark; scheduled target maturity must precede test; no test filtering on future outcomes',
        'rules':{'always':'Rank all signal-time eligible stocks','selective':'Absolute model forecast net return strictly>1%; relative model also requires relative forecast>0. No probability/confidence claim.'},
        'comparators':'Same-horizon absolute control and momentum; recorded production rank21 scores additionally for21 only. No claimed production63 forecast.',
        'evaluation':'Top5 independent nonoverlapping cycles, each slot20% capital, unfilled slots cash. Complete schedule retained, including all-cash and unknown outcomes. Exposure-matched controls use same cycle dates and selected count. Actual ten-position retail planner also reported, with full ledgers.',
        'scenarios':{k:list(v) for k,v in SCENARIOS.items()},'policy':asdict(Policy()),'selection':'Per horizon: positive full-period baskets/retail in all3 scenarios; >=8 active complete cycles; complete positive basket and exposure-matched advantages over all controls; positive retail advantage over controls; positive basket advantage in>=2validation folds; >=20 closed trades and drawdown>=-25%. Select only on validation before later evaluation. No production/paper promotion.',
        'surprise_experiment':'Not run: no supplied verified historical consensus/revision data. Individual guidance example is source feasibility only, not a predictive input.',
        'limits':['Current universe and industry membership, survivorship and adjusted prices','20-stock pilot;63-session sample may be too small to qualify',
            'Inherited legacy-feature finite-row restriction; unused legacy fundamentals are not predictive features',
            'Historical repeated experimentation; bootstrap intervals descriptive, not multiplicity-adjusted','Retail terminal equity can include open positions; cash interest/taxes/limit fills not modeled']}
    path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=protocol:raise RuntimeError('Frozen protocol differs; choose a new output')
    if not path.exists():atomic_json(path,protocol)
    verify_inputs(protocol,output)
    if (output/'report.json').exists():print('Frozen completed run verified; inspect report.json');return
    print('PROTOCOL FROZEN before training:2 horizons x2 targets x2 rules',flush=True)
    _,bars,sessions,statistics,_=prepare(inputs,base)
    bar_frame=pd.read_parquet(inputs/'bars.parquet')
    raw=pd.read_parquet(inputs/'dataset.parquet',columns=['symbol','industry'])
    if (raw.groupby('symbol').industry.nunique()>1).any():raise RuntimeError('Ambiguous industry map')
    industries=raw.drop_duplicates('symbol').set_index('symbol').industry.to_dict()
    features=pd.read_parquet(previous/'features.parquet');symbols=set(features.symbol)
    reference_scores=pd.concat(refs,ignore_index=True);reference_scores=reference_scores[reference_scores.symbol.isin(symbols)]
    common=features[['symbol','trade_date']].merge(reference_scores,on=['symbol','trade_date'],validate='one_to_one')
    events=load_events();result={};selection={}
    for horizon in (21,63):
        print('BUILD TARGETS',horizon,flush=True);folder=output/f'h{horizon}';folder.mkdir(exist_ok=True)
        labels=execution_targets(features,bars,sessions,events,Policy(horizon=horizon))
        peers=sector_forward_returns(bar_frame,industries,sessions,horizon,events,symbols)
        data=features.merge(labels,on=['symbol','trade_date'],validate='one_to_one').merge(peers,on=['symbol','trade_date'],how='left',validate='one_to_one')
        data['sector_relative_net']=data.execution_net-data.sector_forward
        data.to_parquet(folder/'features_targets.parquet',index=False)
        predictions=[];training={};fold_quality={}
        for f in range(1,6):
            start=folds[f]['start'];dates=sessions[:sessions.index(start)-63]
            train=matured_training(data,dates,start)
            test=data.merge(common.loc[common.fold==f,['symbol','trade_date','fold','score']],on=['symbol','trade_date'],validate='one_to_one')
            if len(train)<1000 or test.empty:raise RuntimeError(f'Insufficient shared training/test rows for h{horizon}/fold{f}')
            receipt={'train_rows':len(train),'test_rows':len(test),'last_training_signal':train.trade_date.max(),
                'last_training_maturity':train.target_maturity.max(),'test_start':start,
                'training_stock_count':train.symbol.nunique(),'training_peer_coverage_min':float(train.sector_coverage.min()),
                'finite_financial_features':{c:int(train[c].notna().sum()) for c in ARMS['financial'] if c.startswith('info_')}}
            training[str(f)]=receipt
            pred=test[['symbol','trade_date','fold']].copy()
            if horizon==21:pred['current_score']=test['score'].to_numpy()
            for target,column in [('absolute','execution_net'),('relative','sector_relative_net')]:
                file=folder/f'{target}_fold{f}.parquet';saved=file.with_suffix('.json')
                if file.exists() and saved.exists():
                    meta=json.loads(saved.read_text())
                    if meta['protocol_sha256']!=sha(path) or meta['sha256']!=sha(file):raise RuntimeError('Prediction checkpoint changed')
                    scores=pd.read_parquet(file)
                    if not scores[['symbol','trade_date']].equals(pred[['symbol','trade_date']]):raise RuntimeError('Checkpoint test keys differ')
                else:
                    print(f'FIT h{horizon}/{target}/fold{f}: {len(train)} train, {len(test)} test',flush=True)
                    model=InformationModel(ARMS['financial']).fit(train.assign(net_return=train[column]))
                    scores=pred[['symbol','trade_date']].copy();scores['score']=model.predict(test)
                    scores.to_parquet(file,index=False);atomic_json(saved,{'sha256':sha(file),'protocol_sha256':sha(path),'training':receipt})
                pred[target+'_score']=scores.score.to_numpy()
                fold_quality[f'{target}_fold{f}']=quality(scores,data,folds[f]['end'])
            predictions.append(pred)
        predicted=pd.concat(predictions,ignore_index=True);predicted.to_parquet(folder/'all_predictions.parquet',index=False)
        def run(which,label):
            return evaluate(predicted[predicted.fold.isin(which)],statistics,bars,sessions,events,folds[min(which)]['start'],folds[max(which)]['end'],horizon,folder/label)
        validation=run([1,2,3],'validation');vf=[run([f],f'validation_fold{f}') for f in (1,2,3)]
        candidates=['absolute_selective','relative_always','relative_selective']
        rejections={n:qualify(validation,vf,n) for n in candidates}
        eligible=[n for n in candidates if not rejections[n]]
        chosen=max(eligible,key=lambda n:validation['strategies'][n]['base']['comparisons']['momentum']['mean_difference']) if eligible else None
        selection[str(horizon)]={'selected':chosen,'rejection_reasons':rejections,'assessment_used':False}
        atomic_json(folder/'selection.json',selection[str(horizon)])
        print(f'VALIDATION h{horizon} locked: {chosen}',flush=True)
        later=run([4,5],'assessment');af=[run([f],f'assessment_fold{f}') for f in (4,5)]
        result[str(horizon)]={'validation':validation,'validation_folds':vf,'assessment':later,'assessment_folds':af,
            'training':training,'quality':fold_quality,'target_coverage':{'rows':len(data),'known_execution_rows':int(data.execution_net.notna().sum()),
                'known_sector_rows':int(data.sector_forward.notna().sum()),'known_relative_rows':int(data.sector_relative_net.notna().sum())}}
    verify_inputs(protocol,output)
    artifacts={str(p.relative_to(output)):sha(p) for p in output.glob('h*/**/*') if p.is_file()}
    report={'status':'historical_research','protocol_sha256':sha(path),'selection':selection,'results':result,'source_audit':audit,
        'artifacts':artifacts,'production_changed':False,'existing_paper_changed':False,'promotion_allowed':False}
    atomic_json(output/'report.json',report);verify_inputs(protocol,output)
    print('COMPLETE: production and existing frozen paper trial unchanged',flush=True)


if __name__=='__main__':main()
