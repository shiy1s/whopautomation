import copy, hashlib, importlib.util, tempfile, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('template',Path(__file__).resolve().parents[1]/'.github/scripts/campaign_template.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)

class Template(unittest.TestCase):
    def valid(self):
        return {'width':1080,'height':1920,'videoRect':{'x':0,'y':536,'width':1080,'height':1080},'fit':'cover','sha256':'a'*64}
    def test_invalid_geometry_and_crop_fail(self):
        for rect in [{'x':0,'y':1000,'width':1080,'height':1080},{'x':1,'y':0,'width':1080,'height':1080}]:
            s=self.valid();s['videoRect']=rect
            with self.assertRaises(ValueError):t.validate_template(s)
        for x in [float('nan'),2,-1]:
            s=self.valid();s['cropX']=x
            with self.assertRaises(ValueError):t.validate_template(s)
    def test_changed_artwork_fails_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'template.png';p.write_bytes(b'unit template')
            s=self.valid();s['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            t.verify_template_file(p,s);p.write_bytes(b'wrong campaign')
            with self.assertRaises(ValueError):t.verify_template_file(p,s)
    def test_background_is_preserved_and_video_uses_declared_slot(self):
        result=';'.join(t.template_filters(self.valid()))
        self.assertIn('[1:v]',result);self.assertIn('overlay=0:536',result);self.assertIn('crop=1080:1080',result)
    def test_qc_rejects_missing_hook_but_ignores_video_changes(self):
        from PIL import Image, ImageDraw
        ref=Image.new('RGB',(1080,1920),'black');ImageDraw.Draw(ref).rectangle((100,200,900,450),fill='white')
        out=ref.copy();ImageDraw.Draw(out).rectangle((0,536,1079,1615),fill='red')
        self.assertAlmostEqual(t.template_similarity(ref,out,self.valid()),1)
        self.assertLess(t.template_similarity(ref,Image.new('RGB',(1080,1920),'black'),self.valid()),0.92)
