import argparse
import json
import sys

from .pilot import run_pilot
from .protocol import InputError, read_json
from .runner import analyze, collect


def main():
    parser = argparse.ArgumentParser(description='Finite idempotency test for an explicitly disposable order-lab deployment')
    sub = parser.add_subparsers(dest='action', required=True)
    pilot = sub.add_parser('pilot'); pilot.add_argument('output')
    analysis = sub.add_parser('analyze'); analysis.add_argument('directory')
    run = sub.add_parser('run')
    for field in ('url', 'database', 'nonce', 'plan', 'output'):
        run.add_argument('--' + field, required=True)
    run.add_argument('--authorized-disposable-target', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'pilot':
            result = run_pilot(args.output)
        elif args.action == 'analyze':
            result = analyze(args.directory)
        else:
            result = collect(args.url, args.database, args.nonce, read_json(args.plan), args.output, authorized=args.authorized_disposable_target)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return {'PASS': 0, 'COUNTEREXAMPLE': 3, 'UNKNOWN': 4}.get(result.get('status'), 0)
    except (InputError, OSError) as error:
        print(json.dumps(dict(status='REFUSED', error=str(error))), file=sys.stderr)
        return 2
