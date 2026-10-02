#!/usr/bin/env python3
"""Train baseline and alternative bundles fresh at each historical series cutoff.

This produces research reports, never releases. No incumbent checkpoint is
reused in any fold. One smoke fold is the minimum integration pilot; normal
budgets train every component with 30 neural epochs and 32 lineup trials.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from train_rolling_bundle import ANALYSIS, train_candidate
from rolling_corpus import build_rolling_manifest, DeferredTraining
from app.services.model_registry import atomic_json


def run_backtests(exports: Path, output: Path, cutoffs: list[str], *,
                  alternative: dict, epochs: int=30,trials: int=32,threads: int=1,seed: int=7):
    output.mkdir(parents=True,exist_ok=False);started=time.monotonic();folds=[]
    for index,cutoff in enumerate(cutoffs):
        fold={'cutoff':cutoff,'status':'EXPERIMENTAL','promotion_eligible':False,'policies':{}}
        for name,config in [('baseline',{'mode':'season_recency','decay':.65,'winning_pick_multiplier':1.0}),('alternative',alternative)]:
            try:
                manifest=build_rolling_manifest(exports,cutoff=cutoff,weighting=config)
            except DeferredTraining as error:
                fold['policies'][name]={'status':'DEFERRED','reason':str(error)};continue
            report=train_candidate(manifest,output/f'fold-{index+1}-{name}',epochs=epochs,trials=trials,threads=threads,seed=seed)
            fold['policies'][name]=report
        folds.append(fold);atomic_json(output/'backtest_report.json',{'status':'EXPERIMENTAL','promotion_eligible':False,'folds':folds})
    report={'schema_version':1,'status':'SMOKE/EXPERIMENTAL' if epochs<30 or trials<32 else 'EXPERIMENTAL_BACKTEST',
            'promotion_eligible':False,'wall_seconds':round(time.monotonic()-started,3),'epochs_per_neural_stage':epochs,
            'lineup_trials':trials,'threads':threads,'seed':seed,'folds':folds,
            'interpretation':'Fresh cutoff-trained policy comparisons; one smoke fold does not establish optimal weights or current-season validity.'}
    atomic_json(output/'backtest_report.json',report);return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cutoff',action='append',required=True,help='Repeat for historical/early-season folds (naive timestamps are China time)')
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--alternative',choices=['date_half_life','recent_window'],default='date_half_life')
    parser.add_argument('--half-life-days',type=float,default=120);parser.add_argument('--window-days',type=float,default=180)
    parser.add_argument('--maximum-age-days',type=float)
    parser.add_argument('--epochs',type=int,default=30);parser.add_argument('--trials',type=int,default=32)
    parser.add_argument('--threads',type=int,default=1);parser.add_argument('--seed',type=int,default=7)
    args=parser.parse_args()
    if min(args.epochs,args.trials,args.threads)<1:parser.error('Training budgets must be positive')
    alternative={'mode':args.alternative,'half_life_days':args.half_life_days,'window_days':args.window_days,'winning_pick_multiplier':1.0}
    if args.maximum_age_days:alternative['maximum_age_days']=args.maximum_age_days
    report=run_backtests(ANALYSIS/'exports',args.output_root,args.cutoff,alternative=alternative,epochs=args.epochs,trials=args.trials,threads=args.threads,seed=args.seed)
    print(json.dumps({'status':report['status'],'wall_seconds':report['wall_seconds'],'report':str(args.output_root/'backtest_report.json')},indent=2))

if __name__=='__main__':main()
