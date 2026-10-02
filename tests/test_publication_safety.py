"""Synthetic ledger/browser fixtures. No network calls or real publications."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import runpy
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / '.github/scripts'
ENV = {'GITHUB_REPOSITORY': 'unit/fixture', 'GH_TOKEN': 'unit-only',
       'GITHUB_RUN_ID': '42', 'PHASE10_RUN_ID': '40', 'PHASE11_RUN_ID': '42',
       'CAMPAIGN_ID': 'unit-campaign', 'CAMPAIGN_NAME': 'Unit',
       'CAMPAIGN_STATUS': 'active', 'CAMPAIGN_PLATFORMS': 'youtube',
       'CONFIRM_SUBMISSION': 'SUBMIT_READY'}

def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    stub = types.ModuleType('playwright.sync_api')
    stub.sync_playwright = MagicMock()
    with patch.dict(os.environ, ENV), patch.dict(sys.modules, {'playwright.sync_api': stub}):
        spec.loader.exec_module(module)
    return module

class PublicationSafety(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network'))
        self.network.start()
        self.p = load('phase11-platform-publishing')
        self.clip = {'file': 'unit.mp4', 'sha256': 'unit-hash'}
        self.package = {'campaignId': 'unit-campaign'}

    def tearDown(self):
        self.network.stop()
        self.env.stop()

    def test_renaming_does_not_bypass_duplicate_detection(self):
        ledger = {'publications': [{'clipFile': 'renamed.mp4', 'platform': 'youtube',
                                   'videoSha256': 'unit-hash', 'status': 'published'}]}
        self.assertTrue(self.p.duplicate(ledger, self.clip, 'youtube'))

    def test_package_without_campaign_duplicate_rule_passes_publisher_contract(self):
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                for d in ['phase7-qa', 'phase7-clips', 'phase9-metadata', 'campaign-rules']:
                    Path(d).mkdir()
                def save(path, data): Path(path).write_text(json.dumps(data), encoding='utf-8')
                md = {'clipNumber': 1, 'file': 'clip_01.mp4', 'durationSeconds': 20}
                for p in ['youtubeShorts', 'tiktok', 'instagram']:
                    md[p] = {'description': 'Unit', 'caption': 'Unit', 'hashtags': []}
                save('phase9-metadata/clip_01.json', md)
                save('phase9-metadata/phase9-metadata-manifest.json', {
                    'complete': True, 'status': 'pass', 'sourcePhase': 8, 'phase8RunId': 8,
                    'phase7RunId': 7, 'campaignId': 'unit', 'campaignName': 'Unit', 'clips': [md]})
                save('phase7-qa/phase7-render-manifest.json', {
                    'complete': True, 'phase': '7_ffmpeg_rendering', 'phase6RunId': 6,
                    'originalAudioPreserved': False, 'campaignBrandingApplied': False,
                    'plans': [{'planId': 'unit-plan', 'durationSeconds': 20, 'segments': [{
                        'assetId': 'unit', 'fileName': 'unit.mp4', 'startSeconds': 0, 'endSeconds': 20}]}]})
                save('phase7-qa/quality_report.json', [{'file': 'clip_01.mp4', 'duration': 20,
                    'width': 1080, 'height': 1920, 'videoCodec': 'h264', 'bitrate': 1000000, 'hasAudio': False}])
                save('campaign-rules/unit.json', {'rules': {'publishing': {'liveDurationDays': 30}}})
                Path('phase7-clips/clip_01.mp4').write_bytes(b'synthetic fixture' * 7000)
                with contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(SCRIPTS / 'phase10-publishing-package.py'), run_name='__main__')
                with patch.object(self.p, 'ROOT', Path('phase10-publishing-package')):
                    package = self.p.manifest()
                policy = package['publishingPolicy']
                self.assertTrue(policy['duplicatePostingForbidden'])
                self.assertFalse(policy['originalAudioPreserved'])
                self.assertFalse(policy['campaignBrandingApplied'])
                self.assertEqual(policy['postLiveMinimumDays'], 30)
            finally:
                os.chdir(old)

    def test_intent_write_failure_prevents_upload(self):
        with patch.object(self.p, 'save_ledger', side_effect=RuntimeError('write failed')), \
             patch.object(self.p, 'youtube') as upload:
            with self.assertRaisesRegex(RuntimeError, 'write failed'):
                self.p.publish_one({'publications': []}, 'sha', self.clip, 'youtube', self.package)
            upload.assert_not_called()

    def test_final_write_failure_leaves_durable_intent_and_blocks_retry(self):
        durable = []
        def save(state, sha):
            if durable: raise RuntimeError('final write failed')
            durable.append(copy.deepcopy(state))
            return 'intent-sha'
        with patch.object(self.p, 'save_ledger', side_effect=save), \
             patch.object(self.p, 'youtube', return_value={'videoId': 'unit-video'}) as upload:
            with self.assertRaisesRegex(RuntimeError, 'final write failed'):
                self.p.publish_one({'publications': []}, 'sha', self.clip, 'youtube', self.package)
            with self.assertRaisesRegex(RuntimeError, 'Unresolved publication'):
                self.p.publish_one(durable[0], 'intent-sha', self.clip, 'youtube', self.package)
            self.assertEqual(upload.call_count, 1)
            self.assertEqual(durable[0]['publications'][0]['campaignId'], 'unit-campaign')

    def test_upload_error_records_uncertainty(self):
        writes = []
        with patch.object(self.p, 'save_ledger', side_effect=lambda s, h: writes.append(copy.deepcopy(s)) or 'sha'), \
             patch.object(self.p, 'youtube', side_effect=RuntimeError('ambiguous response')):
            with self.assertRaisesRegex(RuntimeError, 'ambiguous response'):
                self.p.publish_one({'publications': []}, 'sha', self.clip, 'youtube', self.package)
        self.assertEqual([s['publications'][0]['status'] for s in writes],
                         ['publishing', 'needs_manual_verification'])

    def test_queue_rejects_wrong_campaign_and_does_not_requeue_uncertainty(self):
        q = load('phase14-content-rewards-submission')
        pub = {'campaignId': 'wrong', 'phase11RunId': 42, 'status': 'published',
               'platform': 'youtube', 'videoSha256': 'unit-hash', 'clipFile': 'renamed.mp4',
               'publishedAtUtc': q.now().isoformat(), 'remote': {'videoId': 'unit-video'}}
        prior = {'campaignId': 'unit-campaign', 'platform': 'youtube', 'clipFile': 'old.mp4',
                 'postUrl': 'https://www.youtube.com/shorts/unit-video', 'status': 'needs_manual_verification'}
        def read(path):
            return ({'publications': [pub]} if path == q.LEDGER_PATH else {'submissions': [prior]}), 'sha'
        with patch.object(q, 'gh_api', return_value={'name': 'Phase 11 Platform Publishing',
             'status': 'completed', 'conclusion': 'success'}), patch.object(q, 'read_json', side_effect=read), \
             patch.object(q, 'write_json') as write:
            with self.assertRaisesRegex(RuntimeError, 'campaign identity'):
                q.main()
            pub['campaignId'] = 'unit-campaign'
            q.main()
            write.assert_not_called()

    def test_submission_dry_run_failure_is_not_green_and_never_writes(self):
        self.run_worker(dry=True, fail=True)

    def test_submission_intent_precedes_browser_and_each_result_is_saved(self):
        self.run_worker(dry=False, fail=False)

    def test_interrupted_submission_cannot_report_success_on_retry(self):
        w = load('phase14-playwright-submit')
        with patch.object(w, 'read_queue', return_value=({'submissions': [{'status': 'submitting'}]}, 'sha')), \
             patch.object(sys, 'argv', ['worker', '--dry-run']):
            with self.assertRaisesRegex(RuntimeError, 'Unresolved submission intent'): w.main()
        w.sync_playwright.assert_not_called()

    def run_worker(self, dry, fail):
        w = load('phase14-playwright-submit')
        item = {'campaignId': 'unit-campaign', 'platform': 'youtube', 'clipFile': 'unit.mp4',
                'status': 'queued', 'contentRewardsSubmission': {'status': 'queued'}}
        writes = []
        def write(state, sha, message):
            writes.append(copy.deepcopy(state))
            return {'content': {'sha': 'next'}}
        def process(page, item, is_dry):
            self.assertEqual(len(writes), 0 if dry else 1)
            if fail: raise RuntimeError('Authentication required')
            return {'status': 'validated' if dry else 'submitted'}
        with patch.object(w, 'read_queue', return_value=({'submissions': [item]}, 'sha')), \
             patch.object(w, 'write_queue', side_effect=write), patch.object(w, 'storage_state', return_value={}), \
             patch.object(w, 'artifact'), patch.object(w, 'process', side_effect=process), \
             patch.object(sys, 'argv', ['worker'] + (['--dry-run'] if dry else [])):
            if fail:
                with self.assertRaisesRegex(RuntimeError, 'unresolved results'): w.main()
            else: w.main()
        self.assertEqual([s['submissions'][0]['status'] for s in writes], [] if dry else ['submitting', 'submitted'])

if __name__ == '__main__':
    unittest.main()
