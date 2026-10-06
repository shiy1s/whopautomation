"""Validate model-derived speech evidence; never treat it as human verification."""
import hashlib
import json
import math
import time
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
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('AUDIO_DURATION_INVALID')
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise ValueError('AUDIO_TRANSCRIPT_SEGMENTS_MISSING')
    segments = []
    previous_end = 0
    for item in data['segments']:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise ValueError('AUDIO_TRANSCRIPT_ITEM_INVALID')
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


TRANSCRIPT_SCHEMA = {
    'type': 'object', 'required': ['language', 'segments'],
    'properties': {
        'language': {'type': 'string'},
        'segments': {'type': 'array', 'items': {
            'type': 'object', 'required': ['startSeconds', 'endSeconds', 'text'],
            'properties': {'startSeconds': {'type': 'number'},
                           'endSeconds': {'type': 'number'}, 'text': {'type': 'string'}}}}
    }
}


def transcribe_with_retry(request, models, duration, attempts=3, sleep=time.sleep):
    """Retry invalid model output; never repair it by inventing or truncating speech."""
    last_error = None
    for model in dict.fromkeys(models):
        for attempt in range(attempts):
            try:
                speech = normalize_transcript(json.loads(request(model)), duration)
                speech['model'] = model
                return speech
            except Exception as exc:
                last_error = exc
                print(f'Audio transcription {model} attempt {attempt+1}/{attempts} failed: {type(exc).__name__}', flush=True)
                if attempt + 1 < attempts:
                    sleep(5 * (attempt + 1))
    raise RuntimeError('AUDIO_TRANSCRIPTION_FAILED_AFTER_BOUNDED_RETRIES') from last_error
