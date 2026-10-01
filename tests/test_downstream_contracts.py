"""Synthetic unit fixtures only; none of these files is campaign evidence."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / '.github/scripts'
google = types.ModuleType('google')
genai = types.ModuleType('google.genai')
genai.types = types.SimpleNamespace()
google.genai = genai

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    result = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'google': google, 'google.genai': genai}):
        spec.loader.exec_module(result)
    return result

p5 = module('p5', 'phase5-clip-selection.py')
p6 = module('p6', 'phase6-clip-planning.py')

def asset(identity='googledrive-unit-one', relevance=0.95):
    return {'assetId': identity, 'fileName': 'unit.mp4', 'durationSeconds': 24,
            'frames': [{'frameIndex': i, 'timestampSeconds': i * 4,
                        'campaignRelevance': relevance, 'visualDescription': 'Synthetic unit evidence',
                        'enforcementSignals': [], 'safetyFlags': []} for i in range(1, 6)]}

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')

class DownstreamContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_candidate_ids_do_not_collide_for_same_provider(self):
        first = p5.generate_candidates(asset('googledrive-one'))[0]
        second = p5.generate_candidates(asset('googledrive-two'))[0]
        self.assertNotEqual(first['candidateId'], second['candidateId'])

    def test_irrelevant_or_isolated_frames_do_not_become_candidates(self):
        self.assertEqual(p5.generate_candidates(asset(relevance=0)), [])
        data = asset(relevance=0)
        data['frames'][2]['campaignRelevance'] = 1
        self.assertEqual(p5.generate_candidates(data), [])

    def test_phase5_to_phase6_retains_timestamps_and_render_directives(self):
        a = asset()
        a['durationSeconds'] = 23.999993
        m = {'complete': True, 'analysis': {'assetCount': 1, 'frameCount': 5},
             'assets': [{'analysisFile': 'unit.json'}], 'campaignRules': {'campaignId': 'unit'}}
        for root in ['phase4-source', 'phase4b-source']:
            save(f'{root}/phase4-analysis/phase4-analysis-manifest.json', m)
            save(f'{root}/phase4-analysis/unit.json', a)
        save('campaign-rules/unit.json', {'campaignId': 'unit', 'campaignName': 'Unit', 'rules': {
            'video': {}, 'audio': {'originalAudioMustRemainAudible': True},
            'onScreenText': {'required': True, 'requiredLines': ['UNIT']},
            'assetSource': {'sourceType': 'GoogleDrive'}}})
        def decide(client, rules, candidates):
            return {'clipPlans': [{'segments': [{'candidateId': candidates[0]['candidateId']}],
                                   'rationale': 'Synthetic unit only', 'confidence': 0.9}]}
        with patch.object(p5, 'ai_plan', decide), patch.object(p5, 'require_key', return_value='unit'), \
             patch.object(p5.genai, 'Client', return_value=None, create=True):
            p5.main()
        selection = json.loads(Path('phase5-selection/phase5-selection-manifest.json').read_text())
        self.assertIn('anchorTimestampStart', selection['selections'][0]['segments'][0])
        save('phase5-source/phase5-selection-manifest.json', selection)
        p6.main()
        plan = json.loads(Path('phase6-clip-plan/render-plan.json').read_text())
        self.assertEqual(plan['campaign']['campaignId'], 'unit')
        self.assertEqual(plan['renderDirectives']['onScreenTextOptions'], ['UNIT'])
        self.assertTrue(plan['renderDirectives']['originalAudioMustRemainAudible'])
        segment = plan['clipPlans'][0]['segments'][0]
        self.assertLessEqual(segment['endSeconds'], a['durationSeconds'])
        self.assertEqual(segment['endSeconds'], 23.999)

    def test_metadata_uses_platform_tags_and_no_global_campaign_text(self):
        save('phase8-qc/phase8-video-qc-manifest.json', {'complete': True, 'status': 'pass',
             'checks': {'allDeterministicChecksPass': True}, 'campaignId': 'unit',
             'clipReports': [{'file': 'clip_01.mp4', 'durationSeconds': 20, 'checks': {'valid': True}}]})
        save('campaign-rules/unit.json', {'campaignId': 'unit', 'campaignName': 'Unit', 'rules': {
            'platforms': {p: {'accountTag': '@' + p} for p in ['youtubeShorts', 'tiktok', 'instagram']}}})
        Path('campaign_text.txt').write_text('WRONG CAMPAIGN TEXT')
        runpy.run_path(str(SCRIPTS / 'phase9-metadata.py'), run_name='__main__')
        item = json.loads(Path('phase9-metadata/clip_01.json').read_text())
        for platform, field in [('youtubeShorts', 'description'), ('tiktok', 'caption'), ('instagram', 'caption')]:
            self.assertIn('@' + platform, item[platform][field])
            self.assertNotIn('WRONG CAMPAIGN', item[platform][field])

if __name__ == '__main__':
    unittest.main()
