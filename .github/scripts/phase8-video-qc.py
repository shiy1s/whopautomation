#!/usr/bin/env python3
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

MIN_DURATION = 10.0
MAX_DURATION = 60.0
MIN_BITRATE = 500_000
WIDTH = 1080
HEIGHT = 1920
SAMPLE_INTERVAL = 1.0
OVERLAY_THRESHOLD = 0.85
PIXEL_THRESHOLD = 140
ROI_LOGO = (420, 100, 660, 260)
ROI_TEXT = (100, 300, 980, 430)

ROOT = Path(".")
VIDEO_DIR = ROOT / "phase7-clips"
QA_DIR = ROOT / "phase7-qa"
OUT_DIR = ROOT / "phase8-qc"
FRAMES_DIR = OUT_DIR / "sampled-frames"

for d in (OUT_DIR, FRAMES_DIR):
    d.mkdir(parents=True, exist_ok=True)

phase7 = json.loads((QA_DIR / "phase7-render-manifest.json").read_text(encoding="utf-8"))
quality_report = json.loads((QA_DIR / "quality_report.json").read_text(encoding="utf-8"))

if phase7.get("complete") is not True:
    raise RuntimeError("Phase 7 provenance is not complete.")
if phase7.get("originalAudioPreserved") is not True:
    raise RuntimeError("Phase 7 provenance does not confirm original audio preservation.")
if phase7.get("campaignBrandingApplied") is not True:
    raise RuntimeError("Phase 7 provenance does not confirm campaign branding.")
if len(phase7.get("plans", [])) != 2:
    raise RuntimeError("Phase 7 provenance must contain exactly two plans.")
if len(phase7.get("sourceAssets", [])) != 2:
    raise RuntimeError("Phase 7 provenance must contain exactly two source assets.")

campaign_id = str(phase7.get("campaignId") or phase7.get("campaign", {}).get("campaignId") or "").strip()
if not campaign_id:
    raise RuntimeError("Phase 7 manifest does not identify campaignId.")
rules = json.loads((ROOT / "campaign-rules" / f"{campaign_id}.json").read_text(encoding="utf-8"))
campaign_rules = rules["rules"]
required_text = campaign_rules.get("onScreenText", {}).get("requiredLines", [])
logo_required = bool(campaign_rules.get("branding", {}).get("logoRequired"))
text_required = bool(campaign_rules.get("onScreenText", {}).get("required"))
minimum_duration = max(MIN_DURATION, float(campaign_rules.get("video", {}).get("minimumDurationSeconds") or 10))

expected_text = (ROOT / "campaign_text.txt").read_text(encoding="utf-8").strip() if text_required else ""
if text_required and not expected_text:
    raise RuntimeError("campaign_text.txt is empty.")

if text_required:
    normalized_expected = re.sub(r"\s+", " ", expected_text).strip().lower()
    if not any(normalized_expected == re.sub(r"\s+", " ", x).strip().lower() for x in required_text):
        raise RuntimeError("Rendered campaign_text.txt does not match a persisted required on-screen text line.")

def run(cmd, label, allow_failure=False):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode and not allow_failure:
        raise RuntimeError(f"{label} failed (exit {p.returncode}):\\n{p.stderr[-4000:]}")
    return p

def ffprobe(path):
    raw = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration,bit_rate,size:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,channels,sample_rate,duration",
        "-of", "json", str(path)
    ])
    return json.loads(raw)

def rate_value(value):
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        a, b = value.split("/", 1)
        return float(a) / float(b)
    return float(value)

def extract_frame(video, timestamp, output):
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", str(video),
        "-frames:v", "1", "-y", str(output)
    ], f"Frame extraction at {timestamp:.3f}s")

def overlay_coverage(ref_img, sample_img, roi):
    from PIL import Image
    import numpy as np

    x1, y1, x2, y2 = roi
    ref = np.array(ref_img.convert("L"))[y1:y2, x1:x2]
    sample = np.array(sample_img.convert("L"))[y1:y2, x1:x2]
    mask = ref > 180
    if mask.sum() < 100:
        raise RuntimeError("Overlay reference mask is unexpectedly small.")
    return float((sample[mask] > PIXEL_THRESHOLD).mean())

def detect_black_and_freeze(video):
    black = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-vf", "blackdetect=d=2:pic_th=0.995:pix_th=0.02",
        "-an", "-f", "null", "-"
    ], "black-frame detection", allow_failure=True)
    freeze = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-vf", "freezedetect=n=0.001:d=2",
        "-an", "-f", "null", "-"
    ], "freeze detection", allow_failure=True)

    black_text = black.stderr + black.stdout
    freeze_text = freeze.stderr + freeze.stdout
    black_hits = re.findall(r"black_start:([0-9.]+).*?black_end:([0-9.]+).*?black_duration:([0-9.]+)", black_text)
    freeze_hits = re.findall(r"freeze_start:([0-9.]+).*?freeze_duration:([0-9.]+)", freeze_text)
    return {
        "blackIntervalsOver2s": [
            {"start": float(a), "end": float(b), "duration": float(c)}
            for a, b, c in black_hits
        ],
        "freezeIntervalsOver2s": [
            {"start": float(a), "duration": float(b)}
            for a, b in freeze_hits
        ],
    }

def detect_audio(video):
    p = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-map", "0:a:0",
        "-af", "volumedetect,silencedetect=n=-50dB:d=2",
        "-f", "null", "-"
    ], "audio analysis", allow_failure=True)
    text = p.stderr + p.stdout
    mean = re.findall(r"mean_volume:\s*(-?[0-9.]+) dB", text)
    peak = re.findall(r"max_volume:\s*(-?[0-9.]+) dB", text)
    silences = re.findall(r"silence_start:\s*([0-9.]+)|silence_end:\s*([0-9.]+)", text)
    return {
        "meanVolumeDb": float(mean[-1]) if mean else None,
        "maxVolumeDb": float(peak[-1]) if peak else None,
        "silenceEvents": [float(a or b) for a, b in silences],
    }

report_by_file = {x["file"]: x for x in quality_report}
plans_by_asset = {p["assetId"]: p for p in phase7["plans"]}

videos = sorted(VIDEO_DIR.glob("clip_*.mp4"))
if len(videos) != 2:
    raise RuntimeError(f"Expected exactly 2 Phase 7 videos, found {len(videos)}.")

from PIL import Image

clip_reports = []
fatal_errors = []

for video in videos:
    name = video.name
    if name not in report_by_file:
        fatal_errors.append(f"{name}: missing Phase 7 quality-report entry.")
        continue

    meta = ffprobe(video)
    streams = meta.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video_streams) != 1:
        fatal_errors.append(f"{name}: expected exactly one video stream.")
        continue
    if len(audio_streams) < 1:
        fatal_errors.append(f"{name}: audio stream missing.")

    v = video_streams[0]
    a = audio_streams[0] if audio_streams else {}
    duration = float(meta["format"]["duration"])
    bitrate = int(float(meta["format"].get("bit_rate") or 0))
    fps = rate_value(v.get("avg_frame_rate"))

    checks = {
        "duration10to60": max(MIN_DURATION, minimum_duration) <= duration <= MAX_DURATION,
        "vertical1080x1920": int(v.get("width") or 0) == WIDTH and int(v.get("height") or 0) == HEIGHT,
        "bitrateAtLeast500kbps": bitrate >= MIN_BITRATE,
        "h264Video": v.get("codec_name") == "h264",
        "yuv420p": v.get("pix_fmt") == "yuv420p",
        "audioPresent": bool(audio_streams),
        "aacAudio": a.get("codec_name") == "aac" if audio_streams else False,
        "audioDurationAligned": abs(float(a.get("duration") or duration) - duration) <= 0.75 if audio_streams else False,
        "decodeComplete": False,
        "logoSampledVisible": (not logo_required),
        "requiredTextSampledVisible": (not text_required),
    }

    decode = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"
    ], f"Full decode validation for {name}", allow_failure=True)
    checks["decodeComplete"] = decode.returncode == 0
    if decode.returncode != 0:
        fatal_errors.append(f"{name}: full decode failed.")

    p7 = report_by_file[name]
    if abs(float(p7["duration"]) - duration) > 0.15:
        fatal_errors.append(f"{name}: duration differs from Phase 7 QA report.")

    # Verify the final output still matches the Phase 6 plan associated with this render.
    # The renderer emits clip_01 and clip_02 in plan order.
    rank = int(Path(name).stem.split("_")[-1])
    plan = phase7["plans"][rank - 1]
    if abs(float(plan["durationSeconds"]) - duration) > 0.25:
        fatal_errors.append(f"{name}: duration differs from Phase 7 clip plan.")

    sample_count = max(12, int(math.ceil(duration / SAMPLE_INTERVAL)))
    timestamps = [0.0 if sample_count == 1 else i * max(duration - 0.10, 0.0) / (sample_count - 1) for i in range(sample_count)]
    clip_frame_dir = FRAMES_DIR / Path(name).stem
    clip_frame_dir.mkdir(parents=True, exist_ok=True)

    frame_paths = []
    for i, ts in enumerate(timestamps, 1):
        fp = clip_frame_dir / f"frame_{i:03d}.png"
        extract_frame(video, ts, fp)
        frame_paths.append(fp)

    reference = Image.open(frame_paths[0])
    logo_coverages = []
    text_coverages = []
    for fp in frame_paths:
        img = Image.open(fp)
        logo_coverages.append(overlay_coverage(reference, img, ROI_LOGO))
        text_coverages.append(overlay_coverage(reference, img, ROI_TEXT))

    min_logo = min(logo_coverages)
    min_text = min(text_coverages)
    evidence_indices = {0, len(frame_paths) // 2, len(frame_paths) - 1}
    for idx, fp in enumerate(frame_paths):
        if idx not in evidence_indices:
            fp.unlink(missing_ok=True)
    checks["logoSampledVisible"] = min_logo >= OVERLAY_THRESHOLD
    checks["requiredTextSampledVisible"] = min_text >= OVERLAY_THRESHOLD

    if not checks["logoSampledVisible"]:
        fatal_errors.append(f"{name}: persistent Call of Duty logo failed sampled-frame visibility check (min coverage {min_logo:.3f}).")
    if not checks["requiredTextSampledVisible"]:
        fatal_errors.append(f"{name}: required on-screen text failed sampled-frame visibility check (min coverage {min_text:.3f}).")

    anomalies = detect_black_and_freeze(video)
    audio = detect_audio(video)

    # Black/freeze intervals are recorded as anomalies rather than auto-rejected:
    # a campaign video can intentionally contain short dark/frozen editorial moments.
    clip_reports.append({
        "file": name,
        "durationSeconds": round(duration, 3),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "videoCodec": v.get("codec_name"),
        "pixelFormat": v.get("pix_fmt"),
        "fps": round(fps, 3),
        "bitrate": bitrate,
        "audioCodec": a.get("codec_name"),
        "audioChannels": int(a.get("channels") or 0) if audio_streams else 0,
        "audioSampleRate": int(a.get("sample_rate") or 0) if audio_streams else 0,
        "checks": checks,
        "overlaySampling": {
            "sampleCount": sample_count,
            "intervalSeconds": SAMPLE_INTERVAL,
            "minimumLogoCoverage": round(min_logo, 4),
            "minimumRequiredTextCoverage": round(min_text, 4),
            "threshold": OVERLAY_THRESHOLD,
            "evidenceFramesRetained": len(evidence_indices),
        },
        "blackAndFreezeAnomalies": anomalies,
        "audioAnalysis": audio,
    })

if fatal_errors:
    raise RuntimeError("Phase 8 QC failed:\n- " + "\n- ".join(fatal_errors))

all_checks = all(all(x["checks"].values()) for x in clip_reports)
if not all_checks:
    raise RuntimeError("Phase 8 QC failed: one or more deterministic checks are false.")

out = {
    "schemaVersion": "1.0",
    "phase": "8_video_qc",
    "complete": True,
    "status": "pass",
    "sourcePhase": 7,
    "phase7RunId": None,
    "campaignId": rules["campaignId"],
    "campaignName": rules["campaignName"],
    "checks": {
        "exactlyTwoClips": True,
        "allDeterministicChecksPass": True,
        "fullDecodeCompleted": True,
        "campaignLogoSampledVisible": True,
        "requiredOnScreenTextSampledVisible": True,
        "originalAudioStreamPresent": True,
        "durationWithinPhase6Plan": True,
    },
    "clipReports": clip_reports,
    "anomalyPolicy": {
        "blackIntervals": "recorded_for_review_not_auto_rejected",
        "freezeIntervals": "recorded_for_review_not_auto_rejected",
        "silenceEvents": "recorded_for_review_not_auto_rejected",
    },
    "campaignCompliance": {
        "officialSourceProvenance": True,
        "logoRequired": True,
        "logoSampledVisible": True,
        "requiredOnScreenText": expected_text,
        "requiredOnScreenTextSampledVisible": True,
        "minimumDurationSeconds": campaign_rules["video"]["minimumDurationSeconds"],
        "englishOnly": campaign_rules["video"]["englishOnly"],
    },
}

# phase7_run_id is injected by the workflow after this script runs.
Path(OUT_DIR / "phase8-video-qc-manifest.json").write_text(
    json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
)
print("PHASE8_VIDEO_QC_PASS")
print(json.dumps(out, indent=2, ensure_ascii=False))
