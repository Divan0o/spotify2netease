#!/usr/bin/env python3
"""Spotify review, history reservations, and NetEase export workflow."""
import argparse
import json
import sys
from pathlib import Path
from store import Store, DEFAULT_ROOT
import netease
from discovery import plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Read-only ncm-cli installation, credential, and login check')
    p = sub.add_parser('new')
    p.add_argument('--genre', required=True)
    p.add_argument('--count', type=int, required=True)
    p.add_argument('--mode', choices=['recommend', 'save'], default='recommend')
    p.add_argument('--test', action='store_true')
    p.add_argument('--name')
    p = sub.add_parser('plan', help='Compact next action and proven genre/artist hints; no network')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--batch')
    g.add_argument('--genre')
    for command in ('status', 'pool', 'queries', 'ingest', 'reuse', 'assess', 'select', 'search', 'resolve', 'prepare', 'export', 'render', 'mark-delivered', 'abandon'):
        p = sub.add_parser(command)
        p.add_argument('--batch', required=True)
        if command in ('status', 'assess', 'select', 'search'):
            p.add_argument('--verbose', action='store_true')
        if command == 'ingest':
            p.add_argument('--query', required=True)
            p.add_argument('--artist-uri')
        if command == 'reuse':
            p.add_argument('--from-batch', required=True)
        if command == 'select':
            p.add_argument('--diversity-reason')
        if command == 'resolve':
            p.add_argument('--uri', required=True)
            p.add_argument('--ncm-id', help='Omit to record no match')
            p.add_argument('--reason', required=True)
    sub.add_parser('batches')
    args = parser.parse_args()
    c = args.command
    store = None if c == 'doctor' else Store(args.root)
    if c == 'doctor':
        result = netease.setup_status()
    elif c == 'new':
        result = {'batch_id': store.new(args.genre, args.count, args.mode, args.test, args.name)}
    elif c == 'plan':
        result = plan(store, args.batch, args.genre)
    elif c == 'batches':
        result = [store.summary(bid) for bid in store.read()['batches']]
    elif c == 'status':
        result = store.summary(args.batch)
    elif c == 'pool':
        result = store.pool(args.batch)
    elif c == 'queries':
        result = store.summary(args.batch)['queries']
    elif c == 'ingest':
        result = store.ingest(args.batch, json.load(sys.stdin), args.query, args.artist_uri)
    elif c == 'reuse':
        result = store.reuse(args.batch, args.from_batch, json.load(sys.stdin)['uris'])
    elif c == 'assess':
        store.assess(args.batch, json.load(sys.stdin))
        result = store.summary(args.batch)
    elif c == 'select':
        selection = json.load(sys.stdin)
        result = store.select(args.batch, selection['uris'], args.diversity_reason, selection.get('distinct_reasons'))
    elif c == 'search':
        result = netease.search(store, args.batch)
    elif c == 'resolve':
        netease.resolve(store, args.batch, args.uri, args.ncm_id, args.reason)
        result = store.batch(args.batch)['matching'][args.uri]
    elif c == 'prepare':
        b = store.batch(args.batch)
        if len(b['selected']) != b['count']:
            raise ValueError('Target not reached')
        result = dict(batch_id=b['id'], name=b['name'], mode=b['mode'], test=b['test'],
                      selected=[b['candidates'][u] for u in b['selected']], matching=b['matching'],
                      write_ready=all(b['matching'].get(u, {}).get('status') in ('matched', 'unmatched') for u in b['selected']))
    elif c == 'export':
        result = netease.export(store, args.batch)
    elif c in ('render', 'mark-delivered'):
        result = store.delivery(args.batch, confirmed=c == 'mark-delivered')
    else:
        store.abandon(args.batch)
        result = store.summary(args.batch)
    if c in ('status', 'assess', 'select') and not args.verbose:
        result['spotify_calls'] = len(result.pop('queries', []))
    if c == 'search' and not args.verbose:
        result = {u: {k: v for k, v in m.items() if k != 'searches' and
                  (k != 'candidates' or m['status'] == 'needs_review')} for u, m in result.items()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, netease.NcmError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
