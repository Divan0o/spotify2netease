#!/usr/bin/env python3
"""Read recording-aware history; reservations belong to workflow.py select."""
import argparse
import json
import sys
from pathlib import Path
from store import Store, DEFAULT_ROOT, duplicate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('command', choices=['summary', 'list', 'filter'])
    args = p.parse_args()
    store = Store(args.root)
    history = [h for h in store.read()['history'] if h.get('active', True)]
    if args.command == 'summary':
        result = dict(active=len(history), delivered=sum(h['delivery'] == 'delivered' for h in history),
                      reserved=sum(h['delivery'] != 'delivered' for h in history), database=str(store.path))
    elif args.command == 'list':
        result = history
    else:
        result, seen = [], [h['track'] for h in history]
        for t in json.load(sys.stdin):
            t.setdefault('artists', [s.strip() for s in t['artist'].split(',')])
            conflicts = [dict(uri=h['uri'], kind=k) for h in seen if (k := duplicate(t, h))]
            result.append(dict(track=t, eligible=not conflicts, conflicts=conflicts))
            seen.append(t)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
