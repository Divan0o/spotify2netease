"""Offline regression suite. Never invokes a real Spotify or NetEase write."""
import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from store import Store, duplicate, track_from_entity
import netease
from discovery import plan


def entity(i, artist=None, title=None):
    artist = artist or 'Artist ' + str(i // 2)
    return dict(uri='spotify:track:t' + str(i), type='Song', name=title or 'Song ' + str(i),
                creator=artist, creators=[dict(name=artist, uri='spotify:artist:' + artist)],
                url='https://open.spotify.com/track/t' + str(i), parent=dict(name='Album'),
                playback=dict(duration_ms=180000 + i * 1000))


def reviews(entities):
    return [dict(uri=e['uri'], status='accepted', basis='Synthetic test fixture only',
                 evidence=[dict(kind='knowledge', scope='track', certainty='known',
                                claim='Synthetic genre evidence for offline testing')]) for e in entities]


class FakeClient:
    def __init__(self, entities):
        self.songs = [dict(id='n' + str(i), originalId=100 + i, name=e['name'],
                     fullArtists=[dict(name=c['name']) for c in e['creators']],
                     album=dict(name='Album'), duration=e['playback']['duration_ms'])
                     for i, e in enumerate(entities)]
        self.playlists, self.items, self.calls = [], [], []
        self.create_timeout = self.add_timeout = self.read_error = False

    def call(self, *args):
        self.calls.append(args)
        if args[:2] == ('user', 'info'):
            return dict(id='account')
        if args[:2] == ('search', 'song'):
            q = args[args.index('--keyword') + 1]
            songs = [s for s in self.songs if s['name'] == q or s['fullArtists'][0]['name'] + ' ' + s['name'] == q]
            return dict(records=songs, recordCount=len(songs))
        if args[:2] == ('playlist', 'created'):
            return dict(records=copy.deepcopy(self.playlists), recordCount=len(self.playlists))
        if args[:2] == ('playlist', 'create'):
            p = dict(id='p1', originalId=999, name=args[3])
            self.playlists.append(p)
            if self.create_timeout:
                self.create_timeout = False
                raise netease.NcmError('Timed out after creation')
            return p
        if args[:2] == ('playlist', 'tracks'):
            if self.read_error:
                raise netease.NcmError('Readback unavailable')
            return [dict(id=i) for i in reversed(self.items)]
        if args[:2] == ('playlist', 'add'):
            ids = json.loads(args[args.index('--songIdList') + 1])
            if self.add_timeout:
                self.add_timeout = False
                self.items.extend(ids[:3])
                raise netease.NcmError('Timed out after partial addition')
            self.items.extend(i for i in ids if i not in self.items)
            return ids
        raise AssertionError(args)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def batch(self, count=10, test=False, mode='recommend'):
        es = [entity(i) for i in range(count)]
        bid = self.store.new('fixture genre', count, test=test, mode=mode)
        self.store.ingest(bid, dict(entities=es), 'fixture query')
        self.store.assess(bid, reviews(es))
        return bid, es

    def ready(self, count=10):
        bid, es = self.batch(count)
        client = FakeClient(es)
        self.store.select(bid, [e['uri'] for e in es])
        netease.search(self.store, bid, client)
        return bid, es, client

    def test_ten_track_end_to_end_and_reversed_readback(self):
        bid, es, client = self.ready()
        job = netease.export(self.store, bid, client)
        self.assertEqual(job['status'], 'verified')
        self.assertEqual(len(job['confirmed_ids']), 10)
        self.assertEqual(len(self.store.delivery(bid)['tracks']), 10)
        netease.export(self.store, bid, client)
        self.assertEqual(len(client.playlists), 1)
        self.assertEqual(sum(c[:2] == ('playlist', 'add') for c in client.calls), 1)

    def test_create_timeout_resumes_without_duplicate_playlist(self):
        bid, es, client = self.ready()
        client.create_timeout = True
        with self.assertRaises(netease.NcmError):
            netease.export(self.store, bid, client)
        netease.export(Store(self.tmp.name), bid, client)
        self.assertEqual(len(client.playlists), 1)
        self.assertEqual(len(client.items), 10)

    def test_partial_add_readback_retries_only_missing(self):
        bid, es, client = self.ready()
        client.add_timeout = True
        job = netease.export(self.store, bid, client)
        adds = [json.loads(c[-1]) for c in client.calls if c[:2] == ('playlist', 'add')]
        self.assertEqual([len(x) for x in adds], [10, 7])
        self.assertEqual(job['status'], 'verified')

    def test_readback_failure_does_not_blindly_add(self):
        bid, es, client = self.ready()
        client.read_error = True
        with self.assertRaises(netease.NcmError):
            netease.export(self.store, bid, client)
        self.assertFalse(any(c[:2] == ('playlist', 'add') for c in client.calls))
        self.assertEqual(self.store.batch(bid)['export']['status'], 'created')

    def test_test_batch_cannot_write(self):
        bid, es = self.batch(test=True)
        client = FakeClient(es)
        with self.assertRaises(ValueError):
            netease.export(self.store, bid, client)
        self.assertEqual(client.calls, [])

    def test_recommend_count_allows_missing_ncm(self):
        bid, es = self.batch()
        client = FakeClient(es[:-1])
        self.store.select(bid, [e['uri'] for e in es])
        netease.search(self.store, bid, client)
        job = netease.export(self.store, bid, client)
        self.assertEqual(len(self.store.delivery(bid)['tracks']), 10)
        self.assertEqual(len(job['confirmed_ids']), 9)

    def test_cross_album_duplicate_and_remix(self):
        a = track_from_entity(entity(0))
        b = {**a, 'uri': 'spotify:track:different', 'album': 'Compilation'}
        self.assertEqual(duplicate(a, b), 'recording')
        self.assertIsNone(duplicate(a, {**b, 'title': a['title'] + ' (Remix)'}))
        self.assertEqual(duplicate(a, {**b, 'duration_ms': None}), 'possible')

    def test_cross_batch_recording_rejected_and_possible_requires_evidence(self):
        bid, es = self.batch(1)
        self.store.select(bid, [es[0]['uri']])
        later = self.store.new('fixture genre', 1)
        alt = copy.deepcopy(es[0])
        alt['uri'] = 'spotify:track:alternate'
        self.store.ingest(later, dict(entities=[alt]), 'another album')
        self.store.assess(later, reviews([alt]))
        with self.assertRaisesRegex(ValueError, 'recording'):
            self.store.select(later, [alt['uri']])
        alt['uri'] = 'spotify:track:unknown_duration'
        alt['playback'] = {}
        self.store.ingest(later, dict(entities=[alt]), 'uncertain release')
        self.store.assess(later, reviews([alt]))
        with self.assertRaisesRegex(ValueError, 'Possible'):
            self.store.select(later, [alt['uri']])

    def test_rejection_cache_is_genre_scoped(self):
        bid, es = self.batch(1)
        self.store.assess(bid, [dict(uri=es[0]['uri'], status='rejected', basis='Fixture belongs elsewhere')])
        next_bid = self.store.new('fixture genre', 1)
        self.store.ingest(next_bid, dict(entities=es), 'retry')
        self.assertEqual(self.store.pool(next_bid)[0]['assessment']['status'], 'rejected')
        other = self.store.new('different genre', 1)
        self.store.ingest(other, dict(entities=es), 'new style')
        self.assertIsNone(self.store.pool(other)[0]['assessment'])

    def test_save_count_requires_matches_and_distinct_ncm_ids(self):
        bid, es = self.batch(2, mode='save')
        with self.assertRaises(ValueError):
            self.store.select(bid, [es[0]['uri']])
        self.store.update_batch(bid, 'matching', {e['uri']: dict(status='matched', id='same') for e in es})
        with self.assertRaisesRegex(ValueError, 'distinct'):
            self.store.select(bid, [e['uri'] for e in es], diversity_reason='fixture')
        self.assertEqual(self.store.batch(bid)['selected'], [])

    def test_selected_assessments_cannot_change_and_incomplete_cannot_export(self):
        bid, es = self.batch(10)
        self.store.select(bid, [es[0]['uri']])
        with self.assertRaises(ValueError):
            self.store.assess(bid, reviews([es[0]]))
        client = FakeClient(es)
        with self.assertRaises(ValueError):
            netease.export(self.store, bid, client)
        self.assertEqual(client.calls, [])

    def test_abandon_only_unprepared_batch_releases_reservations(self):
        bid, es = self.batch(1)
        self.store.select(bid, [es[0]['uri']])
        self.store.abandon(bid)
        again, es = self.batch(1)
        self.store.select(again, [es[0]['uri']])
        self.assertEqual(self.store.summary(again)['selected'], 1)

    def test_artist_identity_and_specific_evidence_required(self):
        bid = self.store.new('genre', 1)
        es = [entity(0)]
        self.store.ingest(bid, dict(entities=es), 'wrong seed', 'spotify:artist:wrong')
        with self.assertRaises(ValueError):
            self.store.assess(bid, reviews(es))
        self.store.ingest(bid, dict(entities=es), 'right seed', es[0]['creators'][0]['uri'])
        rs = reviews(es)
        rs[0]['evidence'][0]['scope'] = 'artist'
        with self.assertRaises(ValueError):
            self.store.assess(bid, rs)
        self.store.assess(bid, reviews(es))

    def test_playlist_and_lexical_title_not_auto_accepted(self):
        bid = self.store.new('genre', 1)
        self.store.ingest(bid, dict(entities=[dict(uri='spotify:playlist:x', type='Playlist'), entity(0, title='genre')]), 'q')
        self.assertEqual(self.store.summary(bid)['queries'][0]['title_hits'], 1)
        self.assertEqual(len(self.store.pool(bid)), 1)
        p = plan(self.store, bid)
        self.assertEqual(p['action'], 'review')
        self.assertEqual(p['review'][0]['track']['uri'], 'spotify:track:t0')
        with self.assertRaises(ValueError):
            self.store.select(bid, ['spotify:track:t0'])
        self.store.assess(bid, reviews([entity(0, title='genre')]))
        self.assertEqual(plan(self.store, bid)['select_uris'], ['spotify:track:t0'])
        self.store.select(bid, ['spotify:track:t0'])
        self.assertEqual(self.store.batch(bid)['selected'], ['spotify:track:t0'])

    def test_atomic_rollback_when_last_track_conflicts(self):
        first, es = self.batch(1)
        self.store.select(first, [es[0]['uri']])
        second, all_es = self.batch(2)
        with self.assertRaises(ValueError):
            self.store.select(second, [all_es[1]['uri'], all_es[0]['uri']], diversity_reason='fixture')
        self.assertEqual(self.store.batch(second)['selected'], [])
        self.assertEqual(len(self.store.read()['history']), 1)

    def test_concurrent_reservations_only_one_wins(self):
        a, es = self.batch(1)
        b, es = self.batch(1)
        def reserve(bid):
            try:
                Store(self.tmp.name).select(bid, [es[0]['uri']])
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(reserve, [a, b])), [False, True])

    def test_diversity_rollback_and_documented_override(self):
        bid, es = self.batch(2)
        with self.assertRaises(ValueError):
            self.store.select(bid, [e['uri'] for e in es])
        self.assertEqual(self.store.batch(bid)['selected'], [])
        self.store.select(bid, [e['uri'] for e in es], diversity_reason='User requests one artist')

    def test_resume_ready_and_delivered_never_downgraded(self):
        bid, es = self.batch(1)
        self.store.select(bid, [es[0]['uri']])
        restored = Store(self.tmp.name)
        self.assertEqual(restored.summary(bid)['selected'], 1)
        self.assertEqual(restored.delivery(bid)['delivery'], 'ready')
        with self.assertRaises(ValueError):
            restored.abandon(bid)
        restored.delivery(bid, confirmed=True)
        self.assertEqual(restored.delivery(bid)['delivery'], 'delivered')

    def test_search_cached_and_manual_id_restricted(self):
        bid, es = self.batch(1)
        client = FakeClient(es)
        self.store.select(bid, [es[0]['uri']])
        netease.search(self.store, bid, client)
        n = len(client.calls)
        netease.search(self.store, bid, client)
        self.assertEqual(len(client.calls), n)
        with self.assertRaises(ValueError):
            netease.resolve(self.store, bid, es[0]['uri'], 'invented', 'same title')

    def test_shell_metacharacters_passed_as_literal_arguments(self):
        text = "Artist $(touch /tmp/unwanted) ' Song"
        with patch('netease.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"code":200,"data":{}}'
            netease.Client().call('search', 'song', '--keyword', text)
            self.assertEqual(run.call_args.args[0][-1], text)
            self.assertFalse(run.call_args.kwargs['shell'])

    def test_business_error_with_zero_exit_is_failure(self):
        with patch('netease.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"code":500,"message":"failure"}'
            with self.assertRaises(netease.NcmError):
                netease.Client().call('user', 'info')

    def test_plan_reuses_only_same_genre_assessed_artist_hints(self):
        bid, es = self.batch(2)
        self.store.assess(bid, [dict(uri=es[1]['uri'], status='uncertain', basis='No evidence')])
        before = self.store.read()
        result = plan(self.store, genre='fixture genre')
        self.assertEqual(len(result['seeds']), 1)
        self.assertEqual(len(result['seeds'][0]['evidence_tracks']), 1)
        self.assertEqual(plan(self.store, genre='another genre')['seeds'], [])
        self.assertEqual(self.store.read(), before)

    def test_plan_uses_cache_then_artist_suffix_with_genre_suffix_for_cold_start(self):
        before = self.store.read()
        self.assertEqual(plan(self.store, genre='fixture genre')['entry_route'], 'genre_track_suffix')
        self.assertEqual(self.store.read(), before)
        source, es = self.batch(1)
        self.assertEqual(plan(self.store, genre='fixture genre')['entry_route'], 'reuse_cache')
        self.store.select(source, [es[0]['uri']])
        target = self.store.new('fixture genre', 1)
        before = self.store.read()
        p = plan(self.store, target)
        self.assertTrue(p['seeds'])
        self.assertEqual(p['cached_candidates'], [])
        self.assertEqual(p['entry_route'], 'artist_track_suffix')
        self.assertEqual(p['action'], 'discover')
        self.assertEqual(self.store.read(), before)

    def test_plan_review_before_more_discovery_and_limited_window(self):
        bid = self.store.new('fixture genre', 10)
        self.store.ingest(bid, dict(entities=[entity(i) for i in range(30)]), 'query')
        p = plan(self.store, bid)
        self.assertEqual(p['action'], 'review')
        self.assertEqual(len(p['review']), 12)
        self.assertEqual(p['select_uris'], [])

    def test_plan_selects_unique_recordings_then_ends_discovery(self):
        bid, es = self.batch(10)
        p = plan(self.store, bid)
        self.assertEqual(p['action'], 'select')
        self.assertEqual(len(p['select_uris']), 10)
        self.store.select(bid, p['select_uris'])
        p = plan(self.store, bid)
        self.assertEqual(p['action'], 'save_or_deliver')
        self.assertEqual(p['review'], [])
        self.assertEqual(p['remaining'], 0)
        later, es = self.batch(10)
        self.assertEqual(plan(self.store, later)['select_uris'], [])

    def test_plan_excludes_parked_and_artist_identity_mismatch(self):
        bid = self.store.new('fixture genre', 10)
        self.store.ingest(bid, dict(entities=[entity(0)]), 'wrong artist', 'spotify:artist:unknown')
        self.store.ingest(bid, dict(entities=[entity(1)]), 'unknown style')
        self.store.assess(bid, [dict(uri=entity(1)['uri'], status='uncertain', basis='Insufficient evidence')])
        p = plan(self.store, bid)
        self.assertEqual(p['review'], [])
        self.assertEqual(p['action'], 'discover')
        self.assertEqual(p['parked'], 2)

    def test_ncm_queries_selected_only_but_save_mode_can_precheck(self):
        bid, es = self.batch(10)
        client = FakeClient(es)
        self.assertEqual(netease.search(self.store, bid, client), {})
        self.assertEqual(client.calls, [])
        self.store.select(bid, [es[0]['uri']])
        netease.search(self.store, bid, client)
        self.assertEqual(len(client.calls), 1)
        save_bid, es = self.batch(1, mode='save')
        matches = netease.search(self.store, save_bid, client)
        self.assertEqual(matches[es[0]['uri']]['status'], 'matched')

    def test_reuse_preserves_spotify_provenance_without_calls_or_reservations(self):
        source, es = self.batch(2)
        self.store.select(source, [es[0]['uri']])
        target = self.store.new('fixture genre', 2)
        p = plan(self.store, target)
        self.assertEqual(p['action'], 'reuse')
        self.assertEqual([x['uri'] for x in p['cached_candidates']], [es[1]['uri']])
        history = copy.deepcopy(self.store.read()['history'])
        result = self.store.reuse(target, source, [es[1]['uri']])
        self.assertEqual(result['spotify_calls_added'], 0)
        b = self.store.batch(target)
        self.assertEqual(b['queries'], [])
        self.assertEqual(b['matching'], {})
        self.assertEqual(b['selected'], [])
        self.assertEqual(self.store.read()['history'], history)
        c = b['candidates'][es[1]['uri']]
        self.assertEqual(c['track'], self.store.batch(source)['candidates'][es[1]['uri']]['track'])
        self.assertEqual(c['source']['batch_id'], source)
        self.assertEqual(c['source']['query_id'], 1)
        self.assertEqual(plan(self.store, target)['action'], 'select')
        self.assertEqual(self.store.reuse(target, source, [es[1]['uri']])['reused_uris'], [])

    def test_reuse_unreviewed_requires_assessment(self):
        source = self.store.new('fixture genre', 1)
        self.store.ingest(source, dict(entities=[entity(0)]), 'real fixture response')
        target = self.store.new('fixture genre', 1)
        self.store.reuse(target, source, [entity(0)['uri']])
        self.assertEqual(plan(self.store, target)['action'], 'review')
        with self.assertRaises(ValueError):
            self.store.select(target, [entity(0)['uri']])

    def test_reuse_conflict_is_atomic_and_rechecked_after_plan(self):
        source, es = self.batch(2)
        target = self.store.new('fixture genre', 2)
        self.assertEqual(len(plan(self.store, target)['cached_candidates']), 2)
        self.store.select(source, [es[1]['uri']])
        with self.assertRaises(ValueError):
            self.store.reuse(target, source, [e['uri'] for e in es])
        self.assertEqual(self.store.batch(target)['candidates'], {})

    def test_reuse_rejects_other_genre_test_abandoned_and_parked_sources(self):
        source, es = self.batch(1)
        target = self.store.new('another genre', 1)
        with self.assertRaises(ValueError):
            self.store.reuse(target, source, [es[0]['uri']])
        target = self.store.new('fixture genre', 1)
        for changes in ({'test': True}, {'status': 'abandoned'}):
            for key, value in changes.items():
                self.store.update_batch(source, key, value)
            with self.assertRaises(ValueError):
                self.store.reuse(target, source, [es[0]['uri']])
            self.assertEqual(plan(self.store, target)['cached_candidates'], [])
            self.store.update_batch(source, 'test', False)
            self.store.update_batch(source, 'status', 'discovering')
        self.store.assess(source, [dict(uri=es[0]['uri'], status='uncertain', basis='Need new evidence')])
        with self.assertRaises(ValueError):
            self.store.reuse(target, source, [es[0]['uri']])

    def test_reuse_does_not_revive_older_accepted_over_new_uncertainty(self):
        source, es = self.batch(1)
        newer, _ = self.batch(1)
        self.store.assess(newer, [dict(uri=es[0]['uri'], status='uncertain', basis='Conflicting evidence')])
        target = self.store.new('fixture genre', 1)
        self.assertEqual(plan(self.store, target)['cached_candidates'], [])
        with self.assertRaises(ValueError):
            self.store.reuse(target, source, [es[0]['uri']])

    def test_reuse_full_cached_batch_can_complete_with_zero_new_spotify_calls(self):
        source, es = self.batch(10)
        target = self.store.new('fixture genre', 10)
        self.store.reuse(target, source, [e['uri'] for e in es])
        self.store.select(target, plan(self.store, target)['select_uris'])
        p = plan(self.store, target)
        self.assertEqual(p['action'], 'save_or_deliver')
        self.assertEqual(p['spotify_calls'], 0)
        self.assertEqual(p['cached_candidates'], [])
        self.assertEqual(len(self.store.delivery(target)['tracks']), 10)

    def test_plan_remembers_nontrack_and_all_rejected_queries(self):
        source = self.store.new('fixture genre', 1)
        self.store.ingest(source, dict(entities=[dict(uri='spotify:playlist:p', type='playlist')]), 'broad recommendation')
        self.store.ingest(source, dict(entities=[entity(0)]), 'ambiguous artist')
        self.store.assess(source, [dict(uri=entity(0)['uri'], status='rejected', basis='Wrong creator')])
        p = plan(self.store, genre='fixture genre')
        self.assertEqual([x['reason'] for x in p['prior_unproductive_queries']], ['no_tracks', 'all_rejected'])
        self.assertEqual(p['entry_route'], 'genre_track_suffix')
        self.assertEqual(plan(self.store, genre='other')['prior_unproductive_queries'], [])

    def test_legacy_migration_once_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'recommended.json'
            raw = json.dumps(dict(version=1, tracks=[dict(uri='spotify:track:old', artist='A', title='T')]))
            p.write_text(raw)
            self.assertEqual(len(Store(root).read()['history']), 1)
            self.assertEqual(len(Store(root).read()['history']), 1)
            self.assertEqual(p.read_text(), raw)

    def test_corrupt_legacy_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'recommended.json').write_text('{')
            with self.assertRaises(ValueError):
                Store(root)


if __name__ == '__main__':
    unittest.main()
