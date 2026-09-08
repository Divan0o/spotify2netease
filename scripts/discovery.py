"""Read-only discovery hints from assessed batches; never recommends by artist alone."""
import math
from store import norm, duplicate


def unproductive_reason(store, state, batch, query):
    """Explain why another identical search has no useful candidate to add."""
    if not query['track_count']:
        return 'no_tracks'
    items = [batch['candidates'][u] for u in query['new_uris']]
    if not items:
        return 'no_new_tracks'
    if all((c.get('assessment') or {}).get('status') == 'rejected' for c in items):
        return 'all_rejected'
    if all(store.conflicts(state, c['track'], batch['id']) for c in items):
        return 'all_reserved'
    if all(not c['identity_ok'] or
           (c.get('assessment') or {}).get('status') in ('uncertain', 'rejected') or
           store.conflicts(state, c['track'], batch['id']) for c in items):
        return 'no_usable_candidates'
    return None


def cached_candidates(store, state, genre, batch=None):
    """Latest usable, unreserved Spotify entities; no automatic genre acceptance."""
    seen, found = set(), []
    for b in reversed(list(state['batches'].values())):
        if (b.get('test') or b['status'] == 'abandoned' or norm(b['genre']) != norm(genre)
                or (batch and b['id'] == batch['id'])):
            continue
        for uri, c in b['candidates'].items():
            if uri in seen:
                continue
            seen.add(uri)
            if batch and uri in batch['candidates']:
                continue
            if (not c['identity_ok'] or (c.get('assessment') or {}).get('status') in ('uncertain', 'rejected')
                    or norm(genre) + ':' + uri in state['rejection_cache']
                    or store.conflicts(state, c['track'])):
                continue
            found.append(dict(source_batch=b['id'], uri=uri, artist=c['track']['artist'],
                              title=c['track']['title'], assessment=(c.get('assessment') or {}).get('status')))
    return sorted(found, key=lambda c: c['assessment'] != 'accepted')


def plan(store, bid=None, genre=None):
    state = store.read()
    batch = state['batches'][bid] if bid else None
    genre = batch['genre'] if batch else genre
    if not genre:
        raise ValueError('Provide --batch or --genre')
    seeds = {}
    for b in state['batches'].values():
        if b.get('test') or b['status'] == 'abandoned' or norm(b['genre']) != norm(genre):
            continue
        for item in b['candidates'].values():
            a, t = item.get('assessment') or {}, item['track']
            # Only primary creators; unaligned legacy arrays must not invent an identity.
            if a.get('status') != 'accepted' or not item['identity_ok'] or not t.get('artist_uris'):
                continue
            if len(t['artist_uris']) != len(t['artists']):
                continue
            uri = t['artist_uris'][0]
            seed = seeds.setdefault(uri, dict(name=t['artists'][0], uri=uri, evidence_tracks={},
                                              direct_calls=0, direct_accepted=0, tried_this_batch=False,
                                              prior_direct_queries=[]))
            seed['evidence_tracks'][t['uri']] = dict(uri=t['uri'], url=t['url'], basis=a['basis'])
        for q in b['queries']:
            seed = seeds.get(q.get('expected_artist'))
            if seed:
                seed['direct_calls'] += 1
                if b['id'] != bid and q['query'] not in seed['prior_direct_queries']:
                    seed['prior_direct_queries'].append(q['query'])
                seed['direct_accepted'] += sum((b['candidates'][u].get('assessment') or {}).get('status') == 'accepted' for u in q['new_uris'])
    if batch:
        tried = {q.get('expected_artist') for q in batch['queries']}
        for s in seeds.values():
            s['tried_this_batch'] = s['uri'] in tried
    ranked = sorted(seeds.values(), key=lambda s: (s['tried_this_batch'],
                    -s['direct_accepted'] / max(1, s['direct_calls']), -len(s['evidence_tracks']), s['name']))
    for s in ranked:
        s['evidence_tracks'] = list(s['evidence_tracks'].values())[:2]
        s['prior_direct_queries'] = s['prior_direct_queries'][-2:]
    cached = cached_candidates(store, state, genre, batch)
    failed, avoid = [], []
    for b in state['batches'].values():
        if b.get('test') or b['status'] == 'abandoned' or norm(b['genre']) != norm(genre):
            continue
        for q in b['queries']:
            reason = unproductive_reason(store, state, b, q)
            if reason:
                failed.append(dict(query=q['query'], reason=reason))
                if b['id'] == bid:
                    avoid.append(q['query'])
    result = dict(genre=genre, seeds=ranked[:8], cached_candidates=cached[:12],
                  prior_unproductive_queries=failed[-8:],
                  entry_route='reuse_cache' if cached else 'artist_track_suffix' if ranked else 'genre_track_suffix',
                  guidance='Reuse unused cached Spotify tracks first. Genre + the literal suffix {track} is an allowed discovery route and the cold-start default. Confirmed artist seeds may use artist + {track}; specific works may use artist - title. Accept title matches into the candidate pool; title_hits alone must not reject candidates or trigger a fallback. Assess genre and deduplicate before selection. Do not prepend the token, drop its braces, append it to broad recommendation prose, or combine multiple subjects. If a route has no eligible output after review, try an artist or specific work. Obey current tool query rules; mixed responses remain possible.')
    if not batch:
        return result
    remaining = batch['count'] - len(batch['selected'])
    cap = max(1, math.ceil(batch['count'] / 5))
    picked = [batch['candidates'][u]['track'] for u in batch['selected']]
    counts = {}
    def primary(t):
        return (t.get('artist_uris') or [norm(t['artists'][0])])[0]
    for t in picked:
        counts[primary(t)] = counts.get(primary(t), 0) + 1
    selected_ids = {batch['matching'].get(u, {}).get('id') for u in batch['selected']}
    candidates = sorted(batch['candidates'].values(), key=lambda c: (
        (c.get('assessment') or {}).get('status') != 'accepted', primary(c['track']) not in seeds))
    ready, review, review_tracks, parked = [], [], [], 0
    window = min(remaining + 2, 12)
    for c in candidates:
        t, a = c['track'], c.get('assessment') or {}
        if t['uri'] in batch['selected']:
            continue
        if not c['identity_ok'] or a.get('status') in ('uncertain', 'rejected'):
            parked += 1
            continue
        if store.conflicts(state, t, bid) or any(duplicate(t, p) for p in picked + review_tracks):
            continue
        key = primary(t)
        if counts.get(key, 0) >= cap and not batch.get('diversity_reason'):
            continue
        if a.get('status') == 'accepted' and len(ready) < remaining:
            m = batch['matching'].get(t['uri'], {})
            if batch['mode'] == 'save' and (m.get('status') != 'matched' or m.get('id') in selected_ids):
                review.append(dict(track=t, next='match'))
                review_tracks.append(t)
                continue
            ready.append(t['uri'])
            picked.append(t)
            counts[key] = counts.get(key, 0) + 1
            selected_ids.add(m.get('id'))
        elif not a and len(review) < window:
            review.append(dict(track=t, next='assess'))
            review_tracks.append(t)
    if batch['status'] == 'abandoned':
        action = 'abandoned'
    elif not remaining:
        action = 'save_or_deliver'
    else:
        action = 'select' if ready else 'review' if review else 'reuse' if cached else 'discover'
    result.update(batch_id=bid, remaining=remaining, action=action, select_uris=ready,
                  review=review[:window] if remaining else [], parked=parked,
                  spotify_calls=len(batch['queries']),
                  cached_candidates=cached[:window] if remaining and batch['status'] != 'abandoned' else [],
                  ncm_search_calls=sum(len(m.get('searches', [])) for m in batch['matching'].values()),
                  avoid_queries=list(dict.fromkeys(avoid))[-8:])
    return result
