"""Transactional batches and recording-aware history. Standard library only."""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_ROOT = Path.home() / '.codex/state/spotify-genre-recommender'


def now():
    return datetime.now(timezone.utc).isoformat()


def norm(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    return ''.join(c for c in value if c.isalnum())


def title_key(value):
    # Featured credits are identities, not remix/version labels.
    value = re.sub(r'\s*[\[(](?:feat\.?|ft\.?)\s+[^\])]*[\])]', '', value, flags=re.I)
    return norm(value)


def people(track):
    return sorted({norm(x) for x in track.get('artists', []) if norm(x)})


def duplicate(a, b):
    """Return exact / recording / possible / None; never merge by title alone."""
    if a['uri'] == b['uri']:
        return 'exact'
    if a.get('isrc') and b.get('isrc') and a['isrc'] == b['isrc']:
        return 'recording'
    if not people(a) or people(a) != people(b) or title_key(a['title']) != title_key(b['title']):
        return None
    da, db = a.get('duration_ms'), b.get('duration_ms')
    if da and db and abs(da - db) <= 500:
        return 'recording'
    return 'possible'


def track_from_entity(e):
    if not str(e.get('uri', '')).startswith('spotify:track:'):
        return None
    if str(e.get('type', '')).casefold() not in ('song', 'track') and e.get('entity_type') != 'MUSIC':
        return None
    creators = e.get('creators') or []
    artists = [c['name'] for c in creators if c.get('name')]
    artist = e.get('creator') or ', '.join(artists)
    title = e.get('name') or e.get('title')
    url = e.get('url') or e.get('spotify_url') or (e.get('playback') or {}).get('url')
    if not artist or not title or not url:
        return None
    if not artists:
        artists = [artist]
    parsed = urlparse(url)
    if not (parsed.scheme == 'spotify' or (parsed.scheme == 'https' and parsed.hostname == 'open.spotify.com')):
        return None
    return dict(uri=e['uri'], artist=artist, artists=artists,
                artist_uris=[c['uri'] for c in creators if c.get('uri')],
                title=title, url=url, album=(e.get('parent') or {}).get('name'),
                duration_ms=(e.get('playback') or {}).get('duration_ms'),
                isrc=(e.get('external_ids') or {}).get('isrc'))


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'workflow.sqlite3'
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM state WHERE id=1').fetchone() is None:
                history = []
                legacy = self.root / 'recommended.json'
                if legacy.exists():
                    raw = json.loads(legacy.read_text(encoding='utf-8'))
                    if raw.get('version') != 1 or not isinstance(raw.get('tracks'), list):
                        raise ValueError('Invalid legacy history; migration cancelled')
                    for t in raw['tracks']:
                        if not isinstance(t, dict) or not all(isinstance(t.get(k), str) and t[k] for k in ('uri', 'artist', 'title')):
                            raise ValueError('Malformed legacy history entry; migration cancelled')
                        history.append(dict(track={**t, 'artists': [s.strip() for s in t['artist'].split(',')]},
                                            batch_id='legacy', delivery='delivered', saved=None, active=True))
                state = dict(version=2, history=history, batches={}, migrated_at=now(), rejection_cache={}, setup={})
                db.execute('INSERT INTO state VALUES (1, ?)', (json.dumps(state, ensure_ascii=False),))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.execute('PRAGMA synchronous=FULL')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])
            yield state
            db.execute('UPDATE state SET payload=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self):
        with closing(self.connect()) as db:
            return json.loads(db.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])

    def setup_status(self):
        setup = self.read().get('setup', {})
        return {
            component: {
                'confirmed': bool((setup.get(component) or {}).get('confirmed_at')),
                'confirmed_at': (setup.get(component) or {}).get('confirmed_at'),
            }
            for component in ('spotify', 'ncm')
        }

    def confirm_setup(self, component):
        if component not in ('spotify', 'ncm'):
            raise ValueError('Unknown setup component')
        with self.transaction() as state:
            setup = state.setdefault('setup', {})
            setup[component] = {'confirmed_at': now()}
        return self.setup_status()

    def reset_setup(self, component):
        if component not in ('spotify', 'ncm', 'all'):
            raise ValueError('Unknown setup component')
        with self.transaction() as state:
            setup = state.setdefault('setup', {})
            if component == 'all':
                setup.clear()
            else:
                setup.pop(component, None)
        return self.setup_status()

    def batch(self, bid):
        return self.read()['batches'][bid]

    def new(self, genre, count, mode='recommend', test=False, name=None):
        if not genre.strip() or count < 1 or mode not in ('recommend', 'save'):
            raise ValueError('Need a genre, positive count, and recommend/save mode')
        if test and self.root.resolve() == DEFAULT_ROOT.resolve():
            raise ValueError('Tests require an isolated --root')
        bid = datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        with self.transaction() as s:
            s['batches'][bid] = dict(id=bid, genre=genre, count=count, mode=mode, test=test,
                name=name or datetime.now().strftime('%y%m%d') + ' ' + genre,
                created_at=now(), status='discovering', queries=[], candidates={}, selected=[],
                matching={}, export={}, delivery='pending', diversity_reason=None)
        return bid

    def ingest(self, bid, response, query, expected_artist=None):
        data = response.get('structuredContent', response)
        if response.get('isError') or data.get('is_error'):
            raise ValueError(json.dumps(data, ensure_ascii=False))
        if 'entities' not in data:
            for block in response.get('content', []):
                if block.get('type') == 'text':
                    try:
                        candidate = json.loads(block['text'])
                        if 'entities' in candidate:
                            data = candidate
                            break
                    except (ValueError, TypeError):
                        pass
        if not isinstance(data.get('entities'), list):
            raise ValueError('Spotify response has no entities array')
        with self.transaction() as s:
            b = s['batches'][bid]
            qid = len(b['queries']) + 1
            q = dict(id=qid, query=query, at=now(), returned=len(data['entities']), track_count=0,
                     new_uris=[], wrong_artist=[], title_hits=0, expected_artist=expected_artist)
            for e in data['entities']:
                t = track_from_entity(e)
                if t is None:
                    continue
                q['track_count'] += 1
                q['title_hits'] += norm(t['title']) == norm(b['genre'])
                if t['uri'] in b['candidates']:
                    old = b['candidates'][t['uri']]
                    if not old['identity_ok'] and expected_artist and expected_artist in t['artist_uris']:
                        old.update(track=t, query_id=qid, identity_ok=True, assessment=None)
                        q['new_uris'].append(t['uri'])
                    continue
                q['new_uris'].append(t['uri'])
                cached = s['rejection_cache'].get(norm(b['genre']) + ':' + t['uri'])
                item = dict(track=t, query_id=qid, assessment=cached, identity_ok=True)
                if expected_artist and expected_artist not in t['artist_uris']:
                    q['wrong_artist'].append(t['uri'])
                    item['identity_ok'] = False
                    item['assessment'] = dict(status='rejected', basis='Expected artist URI absent from returned creators', evidence=[])
                b['candidates'][t['uri']] = item
            b['queries'].append(q)
        return q

    def assess(self, bid, reviews):
        with self.transaction() as s:
            b = s['batches'][bid]
            for r in reviews:
                if r['uri'] in b['selected']:
                    raise ValueError('Selected assessments are frozen; do not change an existing recommendation')
                item = b['candidates'][r['uri']]
                status = r.get('status')
                if status not in ('accepted', 'uncertain', 'rejected') or not r.get('basis', '').strip():
                    raise ValueError('Assessment needs status and concrete basis')
                if status == 'accepted':
                    if not item['identity_ok']:
                        raise ValueError('Cannot accept an artist identity mismatch; use a correct Spotify entity')
                    evidence = r.get('evidence', [])
                    if not evidence or not any(e.get('scope') in ('track', 'release') and e.get('claim') and
                        ((e.get('kind') == 'external' and str(e.get('url', '')).startswith('https://')) or
                         (e.get('kind') == 'knowledge' and e.get('certainty') == 'known')) for e in evidence):
                        raise ValueError('Accepted requires specific track/release evidence; artist membership alone is insufficient')
                item['assessment'] = {k: v for k, v in r.items() if k != 'uri'}
                key = norm(b['genre']) + ':' + r['uri']
                if status == 'rejected':
                    s['rejection_cache'][key] = item['assessment']
                else:
                    s['rejection_cache'].pop(key, None)

    def conflicts(self, state, track, bid=None):
        return [dict(uri=h['track']['uri'], batch_id=h['batch_id'], kind=kind)
                for h in state['history'] if h.get('active', True)
                if (kind := duplicate(track, h['track'])) and not (h['batch_id'] == bid and h['track']['uri'] == track['uri'])]

    def reuse(self, bid, source_bid, uris):
        """Copy unused Spotify candidates, not recommendations or matching state."""
        with self.transaction() as s:
            b, source = s['batches'][bid], s['batches'][source_bid]
            if bid == source_bid or b['status'] != 'discovering' or b['delivery'] != 'pending':
                raise ValueError('Reuse requires a different source and an open target batch')
            if source.get('test') or source['status'] == 'abandoned' or norm(source['genre']) != norm(b['genre']):
                raise ValueError('Reuse requires non-test, non-abandoned, same-genre Spotify candidates')
            imported = []
            for uri in dict.fromkeys(uris):
                if uri in b['candidates']:
                    continue
                item = source['candidates'][uri]
                for newer in reversed(list(s['batches'].values())):
                    if (newer['id'] == bid or newer.get('test') or newer['status'] == 'abandoned'
                            or norm(newer['genre']) != norm(b['genre']) or uri not in newer['candidates']):
                        continue
                    latest = newer['candidates'][uri]
                    if not latest['identity_ok'] or (latest.get('assessment') or {}).get('status') in ('uncertain', 'rejected'):
                        raise ValueError('Latest cached assessment does not permit automatic reuse: ' + uri)
                    break
                if (not item['identity_ok'] or
                    (item.get('assessment') or {}).get('status') in ('uncertain', 'rejected') or
                    norm(b['genre']) + ':' + uri in s['rejection_cache']):
                    raise ValueError('Cached candidate needs identity/genre re-review in its source batch')
                if self.conflicts(s, item['track']):
                    raise ValueError('Cached recording was recommended/reserved or has a possible conflict: ' + uri)
                # Keep the original query chain; local reuse must not count as a Spotify call.
                copied = copy.deepcopy(item)
                copied.update(query_id=None, source=dict(batch_id=source_bid, uri=uri,
                                                        query_id=item.get('query_id')), reused_at=now())
                b['candidates'][uri] = copied
                imported.append(uri)
        return dict(batch_id=bid, source_batch=source_bid, reused_uris=imported, spotify_calls_added=0)

    def pool(self, bid):
        s = self.read()
        b = s['batches'][bid]
        result = []
        for uri, c in b['candidates'].items():
            if uri in b['selected']:
                continue
            result.append({**c, 'conflicts': self.conflicts(s, c['track'], bid)})
        return result

    def select(self, bid, uris, diversity_reason=None, distinct_reasons=None):
        distinct_reasons = distinct_reasons or {}
        with self.transaction() as s:
            b = s['batches'][bid]
            if b['status'] == 'abandoned':
                raise ValueError('Batch was abandoned')
            if len(set(uris)) != len(uris) or len(b['selected']) + len([u for u in uris if u not in b['selected']]) > b['count']:
                raise ValueError('Selection exceeds target or contains duplicate URIs')
            for uri in uris:
                if uri in b['selected']:
                    continue
                c = b['candidates'][uri]
                if (c.get('assessment') or {}).get('status') != 'accepted' or not c['identity_ok']:
                    raise ValueError('Only assessed, identity-checked tracks may be selected')
                conflicts = self.conflicts(s, c['track'], bid)
                if any(x['kind'] != 'possible' for x in conflicts):
                    raise ValueError('Previously recommended or reserved recording: ' + uri)
                if conflicts and not distinct_reasons.get(uri):
                    raise ValueError('Possible repeated recording requires explicit distinct-version evidence: ' + uri)
                if b['mode'] == 'save' and b['matching'].get(uri, {}).get('status') != 'matched':
                    raise ValueError('Save-count mode selects only confirmed NetEase matches')
                if b['mode'] == 'save' and any(b['matching'][u]['id'] == b['matching'][uri]['id'] for u in b['selected']):
                    raise ValueError('Save-count mode requires distinct NetEase songs')
                s['history'].append(dict(track=c['track'], batch_id=bid, delivery='pending', saved=False,
                    active=True, genre=b['genre'], selected_at=now(), distinct_reason=distinct_reasons.get(uri)))
                b['selected'].append(uri)
            counts = {}
            for uri in b['selected']:
                primary = b['candidates'][uri]['track']['artist_uris'][:1] or people(b['candidates'][uri]['track'])[:1]
                key = primary[0] if primary else uri
                counts[key] = counts.get(key, 0) + 1
            cap = max(1, math.ceil(b['count'] / 5))
            if counts and max(counts.values()) > cap and not (diversity_reason or b['diversity_reason']):
                raise ValueError('Soft artist limit exceeded; diversify or provide --diversity-reason')
            b['diversity_reason'] = diversity_reason or b['diversity_reason']
            b['status'] = 'selected' if len(b['selected']) == b['count'] else 'discovering'
        return self.summary(bid)

    def summary(self, bid):
        s = self.read()
        b = s['batches'][bid]
        qs = []
        for q in b['queries']:
            accepted = [u for u in q['new_uris'] if (b['candidates'][u].get('assessment') or {}).get('status') == 'accepted']
            qs.append({**q, 'accepted': len(accepted), 'usable_new': sum(not self.conflicts(s, b['candidates'][u]['track'], bid) for u in accepted)})
        return dict(id=bid, genre=b['genre'], target=b['count'], mode=b['mode'], status=b['status'],
            selected=len(b['selected']), remaining=b['count']-len(b['selected']),
            matched=sum(m.get('status') == 'matched' for m in b['matching'].values()),
            confirmed=len(b['export'].get('confirmed_ids', [])), delivery=b['delivery'], queries=qs)

    def delivery(self, bid, confirmed=False):
        with self.transaction() as s:
            b = s['batches'][bid]
            if len(b['selected']) != b['count'] or b['status'] == 'abandoned':
                raise ValueError('Batch is not ready for delivery')
            b['delivery'] = 'delivered' if confirmed or b['delivery'] == 'delivered' else 'ready'
            for h in s['history']:
                if h['batch_id'] == bid:
                    h['delivery'] = b['delivery']
            tracks = [b['candidates'][u]['track'] for u in b['selected']]
        return dict(batch_id=bid, delivery=b['delivery'], tracks=tracks, export=b['export'])

    def abandon(self, bid):
        with self.transaction() as s:
            b = s['batches'][bid]
            if b['delivery'] in ('ready', 'delivered') or b['export'].get('playlist') or b['export'].get('status') == 'creating':
                raise ValueError('Cannot release a batch with prepared output or external side effects')
            b['status'] = 'abandoned'
            for h in s['history']:
                if h['batch_id'] == bid:
                    h['active'] = False

    def update_batch(self, bid, field, value):
        with self.transaction() as s:
            s['batches'][bid][field] = copy.deepcopy(value)
            if field == 'export':
                confirmed = set(value.get('confirmed_ids', []))
                for h in s['history']:
                    if h['batch_id'] == bid:
                        match = s['batches'][bid]['matching'].get(h['track']['uri'], {})
                        h['saved'] = bool(match.get('id') in confirmed)
