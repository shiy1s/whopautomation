"""Explicit campaign template composition, with content identity and bounds checks."""
import hashlib
import math
import re
import subprocess
import sys
from pathlib import Path


def validate_template(spec):
    if not isinstance(spec, dict):
        raise ValueError('CAMPAIGN_TEMPLATE_INVALID')
    if (spec.get('width'), spec.get('height')) != (1080, 1920):
        raise ValueError('CAMPAIGN_TEMPLATE_CANVAS_MUST_BE_1080X1920')
    rect = spec.get('videoRect') or {}
    for key in ('x', 'y', 'width', 'height'):
        if type(rect.get(key)) is not int or rect[key] < 0 or rect[key] % 2:
            raise ValueError('CAMPAIGN_TEMPLATE_RECT_INVALID')
    if rect['width'] < 2 or rect['height'] < 2 or rect['x']+rect['width'] > 1080 or rect['y']+rect['height'] > 1920:
        raise ValueError('CAMPAIGN_TEMPLATE_RECT_OUT_OF_BOUNDS')
    if spec.get('fit') not in ('cover', 'contain'):
        raise ValueError('CAMPAIGN_TEMPLATE_FIT_INVALID')
    for key in ('cropX', 'cropY'):
        value = float(spec.get(key, 0.5))
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('CAMPAIGN_TEMPLATE_CROP_INVALID')
    if not re.fullmatch(r'[a-f0-9]{64}', str(spec.get('sha256') or '')):
        raise ValueError('CAMPAIGN_TEMPLATE_SHA256_REQUIRED')
    return spec


def verify_template_file(path, spec):
    validate_template(spec)
    path=Path(path)
    if not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError('CAMPAIGN_TEMPLATE_FILE_INVALID')
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError('CAMPAIGN_TEMPLATE_HASH_MISMATCH')


def acquire_template(spec, root=Path('render-assets')):
    validate_template(spec)
    file_id=str(spec.get('sourceFileId') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', file_id):
        raise ValueError('CAMPAIGN_TEMPLATE_DRIVE_ID_INVALID')
    expected='https://drive.google.com/file/d/'+file_id+'/view'
    if spec.get('sourceUrl') != expected:
        raise ValueError('CAMPAIGN_TEMPLATE_SOURCE_MISMATCH')
    root.mkdir(parents=True, exist_ok=True)
    path=root/(spec['sha256']+'.png')
    if not path.exists():
        subprocess.run([sys.executable,'-m','gdown',expected,'-O',str(path)],check=True,timeout=120)
    verify_template_file(path,spec)
    return path


def template_filters(spec, input_index=1):
    validate_template(spec)
    r=spec['videoRect'];w,h=r['width'],r['height']
    if spec['fit']=='cover':
        fit=f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:(iw-ow)*{float(spec.get('cropX',0.5))}:(ih-oh)*{float(spec.get('cropY',0.5))}"
    else:
        fit=f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    return [f'[{input_index}:v]setsar=1,fps=30[template]',
            f'[0:v]{fit},setsar=1,fps=30[templatevideo]',
            f"[template][templatevideo]overlay={r['x']}:{r['y']}:shortest=1[base]"]


def template_similarity(reference, sample, spec):
    """Compare provided artwork outside the video slot, including bright text."""
    import numpy as np
    validate_template(spec)
    a=np.asarray(reference.convert('RGB'), dtype=np.float32)
    b=np.asarray(sample.convert('RGB'), dtype=np.float32)
    if a.shape != (1920,1080,3) or b.shape != a.shape:
        raise ValueError('CAMPAIGN_TEMPLATE_SAMPLE_DIMENSIONS_INVALID')
    r=spec['videoRect'];mask=np.ones(a.shape[:2],dtype=bool)
    mask[max(0,r['y']-4):r['y']+r['height']+4,max(0,r['x']-4):r['x']+r['width']+4]=False
    if not mask.any():
        raise ValueError('CAMPAIGN_TEMPLATE_NO_VISIBLE_ARTWORK')
    error=np.abs(a-b).mean(axis=2)
    bright=mask & (a.mean(axis=2)>140)
    worst=max(float(error[mask].mean()),float(error[bright].mean()) if bright.any() else 0)
    return 1-worst/255
