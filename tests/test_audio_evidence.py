import importlib.util, hashlib, json, tempfile, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('audio',Path(__file__).resolve().parents[1]/'.github/scripts/phase4b_audio.py')
audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)

class AudioEvidence(unittest.TestCase):
    def test_rejects_bad_timing_and_overlap(self):
        for segments in [[{'startSeconds':0,'endSeconds':11,'text':'a'}],
                         [{'startSeconds':float('nan'),'endSeconds':1,'text':'a'}],
                         [{'startSeconds':0,'endSeconds':2,'text':'a'},{'startSeconds':1,'endSeconds':3,'text':'b'}]]:
            with self.assertRaises(ValueError):audio.normalize_transcript({'segments':segments},10)
    def test_model_speech_is_not_human_or_timing_verified(self):
        result=audio.normalize_transcript({'language':'English','segments':[{'startSeconds':0,'endSeconds':2,'text':'actual speech'}]},10)
        self.assertFalse(result['humanVerified']);self.assertFalse(result['timingVerified'])
    def test_audio_must_match_source_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'audio.mp3').write_bytes(b'isolated-test-audio')
            asset={'contentSha256':'source','audioEvidence':{'status':'extracted','filePath':'audio.mp3','sha256':hashlib.sha256(b'isolated-test-audio').hexdigest(),'sourceContentSha256':'source'}}
            self.assertEqual(audio.load_audio(asset,root),b'isolated-test-audio')
            asset['contentSha256']='different'
            with self.assertRaises(ValueError):audio.load_audio(asset,root)
            asset['contentSha256']='source';(root/'audio.mp3').write_bytes(b'changed')
            with self.assertRaises(ValueError):audio.load_audio(asset,root)
    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):audio.load_audio({'audioEvidence':{'status':'extracted','filePath':'../outside.mp3'}},Path(tmp))
