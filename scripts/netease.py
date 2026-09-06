"""NetEase catalog matching and resumable writes via ncm-cli argument arrays."""
from __future__ import annotations
import fcntl
import json
import shutil
import subprocess
from contextlib import contextmanager
from store import norm, people, title_key, now


class NcmError(RuntimeError):
    pass


def setup_status(which=None, runner=None, client=None):
    """Return a non-secret readiness report for the ncm-cli save workflow."""
    which = which or shutil.which
    runner = runner or subprocess.run
    executable = which('ncm-cli')
    if not executable:
        return {
            'ready': False,
            'status': 'missing',
            'reason': 'ncm-cli is not installed or is not available on PATH',
            'requirements': ['Node.js 18 or newer'],
            'commands': ['npm install -g @music163/ncm-cli', 'ncm-cli --version'],
        }

    try:
        version_result = runner(
            [executable, '--version'], capture_output=True, text=True,
            encoding='utf-8', timeout=10, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            'ready': False,
            'status': 'unavailable',
            'reason': 'ncm-cli could not be executed',
            'commands': ['ncm-cli --version'],
        }
    if version_result.returncode:
        return {
            'ready': False,
            'status': 'unavailable',
            'reason': 'ncm-cli version check failed',
            'commands': ['ncm-cli --version'],
        }
    version = version_result.stdout.strip().splitlines()[0][:100] if version_result.stdout.strip() else None

    missing = []
    for key in ('appId', 'privateKey'):
        try:
            probe = runner(
                [executable, 'config', 'get', key], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            probe = None
        if probe is None or probe.returncode:
            missing.append(key)
    if missing:
        return {
            'ready': False,
            'status': 'configuration_required',
            'version': version,
            'missing': missing,
            'reason': 'ncm-cli API credentials are incomplete',
            'application_url': 'https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL',
            'commands': [
                'ncm-cli config set appId <your-app-id>',
                'ncm-cli config set privateKey <your-private-key>',
            ],
        }

    client = client or Client()
    try:
        user = client.call('user', 'info')
    except NcmError:
        user = None
    if not isinstance(user, dict) or not user.get('id'):
        return {
            'ready': False,
            'status': 'login_required',
            'version': version,
            'reason': 'ncm-cli is configured but no usable NetEase login was detected',
            'commands': ['ncm-cli login --background', 'ncm-cli login --check'],
        }
    return {
        'ready': True,
        'status': 'ready',
        'version': version,
        'reason': 'ncm-cli is installed, configured, and logged in',
    }


class Client:
    def call(self, *args):
        try:
            result = subprocess.run(['ncm-cli', *args], capture_output=True, text=True,
                                    encoding='utf-8', timeout=25, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NcmError(str(exc)) from exc
        if result.returncode:
            raise NcmError(result.stderr.strip() or result.stdout.strip() or 'ncm-cli failed')
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise NcmError('Invalid ncm-cli JSON: ' + result.stdout[:2000]) from exc
        if data.get('code') not in (200, '200'):
            raise NcmError(json.dumps(data, ensure_ascii=False))
        return data.get('data')


@contextmanager
def batch_lock(store, bid):
    if bid not in store.read()['batches']:
        raise ValueError('Unknown batch')
    with (store.root / (bid + '.lock')).open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def compact_song(s):
    return dict(id=s.get('id'), originalId=s.get('originalId'), title=s.get('name'),
                artists=[a['name'] for a in (s.get('fullArtists') or s.get('artists') or []) if a.get('name')],
                album=(s.get('album') or {}).get('name'), duration_ms=s.get('duration'))


def exact_matches(track, songs):
    matches = [s for s in songs if s.get('id') and s.get('title') and people(s) == people(track)
        and title_key(s['title']) == title_key(track['title']) and s.get('duration_ms')
        and track.get('duration_ms') and abs(s['duration_ms'] - track['duration_ms']) <= 1000]
    return sorted(matches, key=lambda s: (norm(s.get('album') or '') != norm(track.get('album') or ''),
                  abs(s['duration_ms'] - track['duration_ms']), str(s['id'])))


def search(store, bid, client=None):
    client = client or Client()
    with batch_lock(store, bid):
        b = store.batch(bid)
        matches = b['matching']
        for uri, item in b['candidates'].items():
            if b['mode'] == 'recommend' and uri not in b['selected']:
                continue
            if (item.get('assessment') or {}).get('status') != 'accepted':
                continue
            previous = matches.get(uri, {})
            if previous.get('status') in ('matched', 'unmatched', 'needs_review'):
                continue
            t = item['track']
            keywords = list(dict.fromkeys([t['artists'][0] + ' ' + t['title'], t['title']]))
            entry = previous or dict(status='searching', searches=[], candidates=[])
            matches[uri] = entry
            for keyword in keywords:
                if any(q['keyword'] == keyword for q in entry['searches']):
                    continue
                try:
                    data = client.call('search', 'song', '--keyword', keyword, '--limit', '10')
                    if not isinstance(data, dict) or not isinstance(data.get('records'), list):
                        raise NcmError('Unexpected search response')
                    songs = [compact_song(s) for s in data['records']]
                    entry['searches'].append(dict(keyword=keyword, at=now()))
                    oldids = {s['id'] for s in entry['candidates']}
                    entry['candidates'].extend(s for s in songs if s['id'] not in oldids)
                    exact = exact_matches(t, entry['candidates'])
                    if exact:
                        entry.update(status='matched', **exact[0], reason='Title, complete artist list, version and duration match')
                    store.update_batch(bid, 'matching', matches)
                    if exact:
                        break
                except NcmError as exc:
                    entry.update(status='error', error=str(exc))
                    store.update_batch(bid, 'matching', matches)
                    raise
            if entry['status'] != 'matched':
                entry['status'] = 'needs_review' if entry['candidates'] else 'unmatched'
                entry['reason'] = 'No sufficiently supported automatic match'
                store.update_batch(bid, 'matching', matches)
        return matches


def resolve(store, bid, uri, ncm_id, reason):
    if not reason.strip():
        raise ValueError('Manual decision requires matching evidence')
    with batch_lock(store, bid):
        b = store.batch(bid)
        if b['export'].get('playlist') or b['export'].get('status') == 'creating':
            raise ValueError('Cannot change matching decisions after export starts')
        m = b['matching'][uri]
        if ncm_id:
            song = next((s for s in m['candidates'] if s['id'] == ncm_id), None)
            if song is None:
                raise ValueError('ID must come from this track\'s cached ncm-cli search results')
            m.update(**song, status='matched', reason=reason)
        else:
            m.update(status='unmatched', reason=reason)
        store.update_batch(bid, 'matching', b['matching'])


def created(client):
    result, offset = {}, 0
    while True:
        data = client.call('playlist', 'created', '--limit', '100', '--offset', str(offset))
        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            raise NcmError('Unexpected playlist listing')
        page = data['records']
        fresh = [p for p in page if p.get('id') not in result]
        result.update((p['id'], p) for p in page if p.get('id'))
        offset += len(page)
        if not page or offset >= int(data.get('recordCount', offset)):
            return list(result.values())
        if not fresh:
            raise NcmError('Playlist pagination did not advance')


def read_ids(client, playlist_id):
    result, offset = set(), 0
    while True:
        data = client.call('playlist', 'tracks', '--playlistId', playlist_id, '--limit', '100', '--offset', str(offset))
        if not isinstance(data, list):
            raise NcmError('Unexpected playlist tracks response')
        ids = {t['id'] for t in data if t.get('id')}
        if data and not ids:
            raise NcmError('Track IDs missing from verification response')
        fresh = ids - result
        result.update(ids)
        if len(data) < 100:
            return result
        if not fresh:
            raise NcmError('Track pagination did not advance')
        offset += len(data)


def export(store, bid, client=None):
    client = client or Client()
    with batch_lock(store, bid):
        b = store.batch(bid)
        if b['test']:
            raise ValueError('Test batch cannot write to NetEase; use read-only prepare')
        if len(b['selected']) != b['count']:
            raise ValueError('Recommendation target not reached')
        undecided = [u for u in b['selected'] if b['matching'].get(u, {}).get('status') not in ('matched', 'unmatched')]
        if undecided:
            raise ValueError('Resolve matching decisions before export: ' + ', '.join(undecided))
        target = {b['matching'][u]['id'] for u in b['selected'] if b['matching'][u]['status'] == 'matched'}
        if not target:
            raise ValueError('No matches; empty playlist will not be created')
        if b['mode'] == 'save' and len(target) != b['count']:
            raise ValueError('Save-count target requires distinct NetEase songs')
        job = b['export'] or dict(status='planned', attempts={}, confirmed_ids=[])
        user = client.call('user', 'info')
        if not isinstance(user, dict) or not user.get('id'):
            raise NcmError('Account identity unavailable')
        if job.get('account_id') and job['account_id'] != user['id']:
            raise ValueError('Account differs from the batch owner')
        job['account_id'] = user['id']
        store.update_batch(bid, 'export', job)
        try:
            if not job.get('playlist'):
                listing = created(client)
                if job['status'] == 'creating':
                    candidates = [p for p in listing if p['id'] not in job['before_ids'] and p.get('name') == job['name']]
                    if len(candidates) != 1:
                        raise NcmError('Creation outcome uncertain; refusing to create another playlist')
                    playlist = candidates[0]
                else:
                    names = {p.get('name') for p in listing}
                    name, suffix = b['name'], 2
                    while name in names:
                        name = b['name'] + ' (' + str(suffix) + ')'
                        suffix += 1
                    job.update(status='creating', name=name, before_ids=[p['id'] for p in listing])
                    store.update_batch(bid, 'export', job)
                    playlist = client.call('playlist', 'create', '--playlistName', name)
                if not isinstance(playlist, dict) or not playlist.get('id'):
                    raise NcmError('Creation returned no playlist ID; reconcile before retrying')
                job.update(playlist=playlist, status='created')
                store.update_batch(bid, 'export', job)
            pid = job['playlist']['id']
            actual = read_ids(client, pid)
            for _ in range(2):
                missing = target - actual
                todo = sorted(u for u in missing if job['attempts'].get(u, 0) < 2)
                if not todo:
                    break
                for start in range(0, len(todo), 50):
                    chunk = todo[start:start + 50]
                    for u in chunk:
                        job['attempts'][u] = job['attempts'].get(u, 0) + 1
                    job['status'] = 'adding'
                    store.update_batch(bid, 'export', job)
                    try:
                        client.call('playlist', 'add', '--playlistId', pid, '--songIdList', json.dumps(chunk))
                    except NcmError as exc:
                        job['error'] = str(exc)
                        store.update_batch(bid, 'export', job)
                        if '请求总量超限' in str(exc):
                            raise
                    actual = read_ids(client, pid)
                    job['confirmed_ids'] = sorted(target & actual)
                    store.update_batch(bid, 'export', job)
            job.update(status='verified' if target <= actual else 'partial',
                       confirmed_ids=sorted(target & actual), missing_ids=sorted(target - actual))
            original = job['playlist'].get('originalId')
            if original and str(original).isdigit():
                job['url'] = 'https://music.163.com/#/playlist?id=' + str(original)
            store.update_batch(bid, 'export', job)
            return job
        except (NcmError, ValueError) as exc:
            job['error'] = str(exc)
            store.update_batch(bid, 'export', job)
            raise
