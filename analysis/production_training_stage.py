#!/usr/bin/env python3
"""Run one production refit stage in a fresh process to bound memory overlap."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=('inputs', 'identity', 'references', 'base', 'familiarity', 'ban', 'lineup', 'catalog'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    from memory_telemetry import record
    record('stage_start', args.stage)
    import production_all_data as production
    manifest = json.loads(args.manifest.read_text())
    production.validate_sources(manifest)
    if args.stage in {'inputs', 'identity'}:
        if args.stage == 'inputs':
            production.ensure_hero_vocabulary(manifest)
        identity, inputs = production.retrain_identity(manifest, {**production.RECIPE, 'seed': args.seed, 'epochs': args.epochs})
        result = {'identity': identity, 'inputs': inputs,
                  'raw_inputs': {str(p): production.file_sha256(p) for p in production.maintained_inputs()}}
    elif args.stage == 'references':
        production.build_references(manifest, args.output)
        result = {}
    elif args.stage == 'base':
        result = production.train_base(manifest, args.output, args.epochs, args.threads, args.seed)
    elif args.stage == 'familiarity':
        result = production.train_familiarity(manifest, args.output, args.epochs, args.threads, args.seed)
    elif args.stage == 'ban':
        result = production.train_ban(manifest, args.output)
    elif args.stage == 'lineup':
        result = production.train_lineup_all_data(manifest, args.output)
    else:
        production.finalize_catalog(args.output, manifest=manifest)
        result = {}
    production.atomic_json(args.result, result)
    record('stage_end', args.stage)


if __name__ == '__main__':
    main()
