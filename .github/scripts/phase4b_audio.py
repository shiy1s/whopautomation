"""Validate model-derived speech evidence; never treat it as human verification."""
import hashlib
import math
from pathlib import Path


def load_audio(asset, root=Path('phase4-input')):
    evidence = asset.get('audioEvidence') or {}
    if evidence.get('status') != 'extracted':
        return None
    path = (root / evidence['filePath']).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('AUDIO_EVIDENCE_PATH_INVALID')
    raw = path.read_bytes()
    if len(raw) > 4_000_000 or hashlib.sha256(raw).hexdigest() != evidence.get('sha256'):
        raise ValueError('AUDIO_EVIDENCE_HASH_OR_SIZE_INVALID')
    if evidence.get('sourceContentSha256') != asset.get('contentSha256'):
        raise ValueError('AUDIO_SOURCE_IDENTITY_MISMATCH')
    return raw


def normalize_transcript(data, duration):
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise ValueError('AUDIO_TRANSCRIPT_SEGMENTS_MISSING')
    segments = []
    previous_end = 0
    for item in data['segments']:
        start, end = float(item['startSeconds']), float(item['endSeconds'])
        text = str(item.get('text') or '').strip()
        if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= duration:
            raise ValueError('AUDIO_TRANSCRIPT_TIME_INVALID')
        if start < previous_end or not text or len(text) > 2000:
            raise ValueError('AUDIO_TRANSCRIPT_ORDER_OR_TEXT_INVALID')
        segments.append({'startSeconds': start, 'endSeconds': end, 'text': text})
        previous_end = end
    return {'status': 'model_transcribed', 'segments': segments,
            'language': str(data.get('language') or 'unknown'),
            'humanVerified': False, 'timingVerified': False,
            'limitations': 'Model transcription and timestamps require listening review before editorial cuts or captions.'}
