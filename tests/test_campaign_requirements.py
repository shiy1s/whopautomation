"""Isolated metadata fixtures; no real media or publishing side effects."""
import contextlib, io, json, os, runpy, tempfile, unittest
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[1]/'.github/scripts/phase9-metadata.py'
class CampaignRequirements(unittest.TestCase):
 def generate(self,rules,duration=20):
  with tempfile.TemporaryDirectory() as tmp:
   old=Path.cwd()
   try:
    os.chdir(tmp)
    Path('phase8-qc').mkdir();Path('campaign-rules').mkdir()
    Path('phase8-qc/phase8-video-qc-manifest.json').write_text(json.dumps({'complete':True,'status':'pass','checks':{'allDeterministicChecksPass':True},'campaignId':'unit','clipReports':[{'file':'clip_01.mp4','durationSeconds':duration,'checks':{'valid':True}}]}))
    Path('campaign-rules/unit.json').write_text(json.dumps({'campaignId':'unit','campaignName':'Unit','rules':rules}))
    with contextlib.redirect_stdout(io.StringIO()):runpy.run_path(str(SCRIPT),run_name='__main__')
    return json.loads(Path('phase9-metadata/clip_01.json').read_text())
   finally:os.chdir(old)
 def test_campaign_maximum_and_invalid_limits_are_enforced(self):
  for limit in [19,0,-1,float('nan')]:
   with self.assertRaises(RuntimeError):self.generate({'video':{'maximumDurationSeconds':limit}})
  self.assertEqual(self.generate({'video':{'maximumDurationSeconds':20}})['durationSeconds'],20)
 def test_required_context_and_disclosure_cannot_be_omitted(self):
  for rules in [{'caption':{'mustGiveContext':True}},{'disclosure':{'required':True}}]:
   with self.assertRaises(RuntimeError):self.generate(rules)
 def test_forbidden_example_hashtags_are_not_generated(self):
  r=self.generate({'rawText':'Never use #Bad. Optional #Example','caption':{'requiredHashtags':['#One','#Two','#Three','#Four'],'forbiddenHashtags':['#Bad']},'platforms':{'youtubeShorts':{'allowed':True},'tiktok':{'allowed':True,'forbidden':True}}})
  self.assertEqual(r['approvedPlatforms'],['youtubeShorts'])
  self.assertNotIn('#Bad',r['youtubeShorts']['description']);self.assertNotIn('#Example',r['youtubeShorts']['description'])
  self.assertEqual(r['youtubeShorts']['hashtags'],['#One','#Two','#Three','#Four'])
 def test_conflicting_hashtag_rules_fail_closed(self):
  with self.assertRaises(RuntimeError):self.generate({'caption':{'requiredHashtags':['#One'],'forbiddenHashtags':['#one']}})
 def test_disclosure_can_precede_other_required_hashtags(self):
  r=self.generate({'caption':{'requiredHashtags':['#One','#Two']},'disclosure':{'required':True,'selected':'#Ad','placement':'first_hashtag_after_text'}})
  self.assertEqual(r['youtubeShorts']['hashtags'],['#Ad','#One','#Two'])
 def test_required_cta_is_preserved_for_every_platform(self):
  r=self.generate({'caption':{'requiredCallToAction':'Install the official app.'}})
  self.assertIn('Install the official app.',r['youtubeShorts']['description'])
  for p in ['instagram','tiktok']:self.assertIn('Install the official app.',r[p]['caption'])
