"""Offline contract fixtures only: no credential, publication, or campaign evidence."""
import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / '.github/scripts'
ENV = {'GITHUB_REPOSITORY': 'unit/fixture', 'GH_TOKEN': 'unit-only',
       'GITHUB_RUN_ID': '42', 'PHASE10_RUN_ID': '40', 'PHASE11_RUN_ID': '42',
       'CAMPAIGN_ID': '00000000-0000-4000-8000-000000000001', 'CAMPAIGN_NAME': 'Unit',
       'CAMPAIGN_STATUS': 'active', 'CAMPAIGN_PLATFORMS': 'youtube,instagram',
       'CONFIRM_SUBMISSION': 'SUBMIT_READY', 'CONFIRM_PUBLISH': 'TEST',
       'YOUTUBE_CLIENT_ID': 'unused-unit', 'YOUTUBE_CLIENT_SECRET': 'unused-unit',
       'YOUTUBE_REFRESH_TOKEN': 'unused-unit', 'INSTAGRAM_ACCESS_TOKEN': 'unused-unit',
       'INSTAGRAM_USER_ID': 'unused-unit'}


def load(name, env=None):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    stub = types.ModuleType('playwright.sync_api')
    stub.sync_playwright = MagicMock()
    with patch.dict(os.environ, {**ENV, **(env or {})}, clear=True), \
         patch.dict(sys.modules, {'playwright.sync_api': stub}):
        spec.loader.exec_module(module)
    return module


class PostPublicationContracts(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network'))
        self.network.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.network.stop)

    def package(self, allowed=None):
        return {'campaignId': ENV['CAMPAIGN_ID'], 'phase9RunId': 39,
                'publishingPolicy': {'approvedPlatforms': allowed or ['youtube', 'instagram']},
                'clips': [{'file': 'unit.mp4', 'platforms': ['youtubeShorts', 'instagram']}]}

    def publication(self, **changes):
        return {**{'phase11RunId': 42, 'phase10RunId': '40', 'campaignId': ENV['CAMPAIGN_ID'],
                'status': 'published', 'platform': 'youtube', 'clipFile': 'unit.mp4',
                'videoSha256': 'unit-hash', 'publishedAtUtc': '2026-10-02T10:00:00Z',
                'remote': {'videoId': 'unit-video', 'privacyStatus': 'public'}}, **changes}

    def queue_item(self):
        p = self.publication()
        return {**p, 'status': 'queued', 'campaignPlatforms': ['youtube'],
                'postUrl': 'https://www.youtube.com/shorts/unit-video',
                'contentRewardsSubmission': {'status': 'queued'}}

    def test_all_selection_intersects_campaign_and_never_adds_tiktok(self):
        p = load('phase11-platform-publishing')
        self.assertEqual(p.selected_platforms(self.package(['youtube'])), ['youtube'])
        self.assertEqual(p.selected_platforms(self.package(['youtube', 'instagram', 'tiktok'])),
                         ['youtube', 'instagram'])

    def test_selection_rejects_unknown_and_explicit_disallowed_platforms(self):
        for selection, expected in [('unknown', 'Unsupported'), ('instagram', 'not approved')]:
            p = load('phase11-platform-publishing', {'PLATFORMS': selection})
            with self.assertRaisesRegex(RuntimeError, expected):
                p.selected_platforms(self.package(['youtube']))

    def test_duplicate_platform_inputs_normalize_without_double_posting(self):
        p = load('phase11-platform-publishing', {'PLATFORMS': ' YouTube ,youtube '})
        self.assertEqual(p.selected_platforms(self.package()), ['youtube'])

    def test_approved_policy_cannot_hide_missing_clip_metadata(self):
        p = load('phase11-platform-publishing')
        m = self.package()
        m['clips'][0]['platforms'] = ['youtubeShorts']
        with self.assertRaisesRegex(RuntimeError, 'lacks approved platform metadata'):
            p.selected_platforms(m)

    def test_test_mode_reports_unreviewed_policy_and_never_reads_ledger(self):
        p = load('phase11-platform-publishing')
        m = self.package()
        m['publishingPolicy'] = {'creativeReviewRequired': True, 'campaignRequirementsVerified': False}
        output = io.StringIO()
        with patch.object(p, 'manifest', return_value=m), patch.object(p, 'ledger') as ledger, \
             contextlib.redirect_stdout(output):
            p.main()
        ledger.assert_not_called()
        data = json.loads(output.getvalue())
        self.assertTrue(data['publishingSkipped'])
        self.assertFalse(data['publishingPolicyReady'])
        self.assertEqual(len(data['productionBlockers']), 2)

    def test_publish_blocks_unreviewed_creative_before_preflight_or_state_write(self):
        p = load('phase11-platform-publishing')
        m = self.package()
        m['publishingPolicy'].update(creativeReviewRequired=True, campaignRequirementsVerified=False)
        with patch.dict(os.environ, {'CONFIRM_PUBLISH': 'PUBLISH', 'PHASE11_PREFLIGHT': '1'}), \
             patch.object(p, 'manifest', return_value=m), patch.object(p, 'youtube_preflight') as preflight, \
             patch.object(p, 'ledger') as ledger:
            with self.assertRaisesRegex(RuntimeError, 'creative requirements'): p.main()
        preflight.assert_not_called()
        ledger.assert_not_called()

    def test_tiktok_upload_acknowledgement_cannot_be_treated_as_published(self):
        p = load('phase11-platform-publishing', {'PLATFORMS': 'tiktok'})
        m = self.package(['tiktok'])
        m['clips'][0]['platforms'] = ['tiktok']
        with patch.dict(os.environ, {'CONFIRM_PUBLISH': 'PUBLISH'}), \
             patch.object(p, 'manifest', return_value=m), patch.object(p, 'ledger') as ledger:
            with self.assertRaisesRegex(RuntimeError, 'completion verification'): p.main()
        ledger.assert_not_called()

    def test_phase12_test_run_cannot_substitute_prior_publications(self):
        p = load('phase12-publication-tracking')
        with patch.object(p, 'validate_phase11_run', return_value={'headSha': 'unit'}), \
             patch.object(p, 'read_json_from_repo', return_value=({'publications': [self.publication(phase11RunId=41)]}, 'sha')), \
             patch.object(p, 'refresh_youtube_token') as token, patch.object(p, 'write_json_to_repo') as write:
            with self.assertRaisesRegex(RuntimeError, 'exact Phase 11 run'): p.main()
        token.assert_not_called()
        write.assert_not_called()

    def test_phase12_retains_exact_campaign_and_run_provenance(self):
        p = load('phase12-publication-tracking')
        pubs = [self.publication(phase11RunId=41), self.publication()]
        with patch.object(p, 'validate_phase11_run', return_value={'headSha': 'unit'}), \
             patch.object(p, 'read_json_from_repo', side_effect=[({'publications': pubs}, 'ledger'), ({'snapshots': []}, 'tracking')]), \
             patch.object(p, 'refresh_youtube_token', return_value='unused'), \
             patch.object(p, 'youtube_snapshot', return_value={'videoId': 'unit-video'}) as snapshot, \
             patch.object(p, 'write_json_to_repo', return_value='written') as write, \
             contextlib.redirect_stdout(io.StringIO()):
            p.main()
        snapshot.assert_called_once_with('unit-video', 'unused')
        record = write.call_args.args[1]['snapshots'][0]
        self.assertEqual(record['campaignId'], ENV['CAMPAIGN_ID'])
        self.assertEqual(record['phase10RunId'], '40')
        self.assertEqual(record['platforms'], ['youtube'])
        self.assertEqual(record['snapshots'][0]['campaignId'], ENV['CAMPAIGN_ID'])

    def test_phase12_rejects_mixed_campaign_provenance(self):
        p = load('phase12-publication-tracking')
        pubs = [self.publication(), self.publication(campaignId='different')]
        with patch.object(p, 'validate_phase11_run', return_value={'headSha': 'unit'}), \
             patch.object(p, 'read_json_from_repo', return_value=({'publications': pubs}, 'sha')):
            with self.assertRaisesRegex(RuntimeError, 'provenance mismatch'): p.main()

    def test_phase12_verifies_returned_youtube_identity(self):
        p = load('phase12-publication-tracking')
        with patch.object(p, 'google_json', return_value={'items': [{'id': 'wrong'}]}):
            with self.assertRaisesRegex(RuntimeError, 'ID mismatch'): p.youtube_snapshot('expected', 'unused')

    def test_queue_canonicalizes_instagram_query_and_hostname_aliases(self):
        q = load('phase14-content-rewards-submission')
        pub = self.publication(platform='instagram', publishedAtUtc=q.now().isoformat(),
                               remote={'permalink': 'https://instagram.com/reel/unit-code?igsh=one'})
        prior = {'campaignId': ENV['CAMPAIGN_ID'], 'platform': 'instagram',
                 'postUrl': 'https://www.instagram.com/reel/unit-code/?igsh=two',
                 'status': 'needs_manual_verification', 'clipFile': 'renamed.mp4'}
        def read(path):
            return ({'publications': [pub]} if path == q.LEDGER_PATH else {'submissions': [prior]}), 'sha'
        with patch.object(q, 'gh_api', return_value={'name': 'Phase 11 Platform Publishing', 'status': 'completed', 'conclusion': 'success'}), \
             patch.object(q, 'read_json', side_effect=read), patch.object(q, 'write_json') as write, \
             contextlib.redirect_stdout(io.StringIO()):
            q.main()
            write.assert_not_called()
            prior['campaignId'] = 'different'
            with self.assertRaisesRegex(RuntimeError, 'different campaign'): q.main()

    def test_queue_rejects_private_youtube_before_write(self):
        q = load('phase14-content-rewards-submission')
        pub = self.publication(publishedAtUtc=q.now().isoformat(), remote={'videoId': 'unit-video', 'privacyStatus': 'private'})
        def read(path):
            return ({'publications': [pub]} if path == q.LEDGER_PATH else {'submissions': []}), 'sha'
        with patch.object(q, 'gh_api', return_value={'name': 'Phase 11 Platform Publishing', 'status': 'completed', 'conclusion': 'success'}), \
             patch.object(q, 'read_json', side_effect=read), patch.object(q, 'write_json') as write:
            with self.assertRaisesRegex(RuntimeError, 'not verified public'): q.main()
        write.assert_not_called()

    def test_worker_rechecks_campaign_url_timestamp_and_publication_binding(self):
        w = load('phase14-playwright-submit')
        item = self.queue_item()
        def encoded(pub): return {'content': base64.b64encode(json.dumps({'publications': [pub]}).encode()).decode()}
        with patch.object(w, 'gh', return_value=encoded(self.publication())):
            w.verify_publication(item)
            changed = copy.deepcopy(item)
            changed['postUrl'] = 'https://www.youtube.com/shorts/wrong'
            with self.assertRaisesRegex(RuntimeError, 'mismatches'): w.verify_publication(changed)
        with patch.object(w, 'gh', return_value=encoded(self.publication(campaignId='different'))):
            with self.assertRaisesRegex(RuntimeError, 'verified campaign publication'): w.verify_publication(item)

    def test_worker_rejects_duplicate_uncertain_url_before_browser(self):
        w = load('phase14-playwright-submit')
        item = self.queue_item()
        prior = {**item, 'status': 'needs_manual_verification', 'clipFile': 'old.mp4',
                 'postUrl': 'https://youtube.com/shorts/unit-video?feature=share'}
        with patch.object(w, 'read_queue', return_value=({'submissions': [item, prior]}, 'sha')), \
             patch.object(sys, 'argv', ['worker', '--dry-run']):
            with self.assertRaisesRegex(RuntimeError, 'Duplicate or conflicting'): w.main()
        w.sync_playwright.assert_not_called()

    def test_worker_cannot_relax_maximum_submission_age(self):
        w = load('phase14-playwright-submit', {'MAX_AGE_MINUTES': '999'})
        with patch.object(sys, 'argv', ['worker', '--dry-run']):
            with self.assertRaisesRegex(RuntimeError, 'between 1 and 30'): w.main()
        w.sync_playwright.assert_not_called()


if __name__ == '__main__':
    unittest.main()
