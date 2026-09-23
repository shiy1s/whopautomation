#!/usr/bin/env python3
import hashlib
import json
import shutil
from pathlib import Path

P9 = Path("phase9-metadata/phase9-metadata-manifest.json")
P7 = Path("phase7-qa/phase7-render-manifest.json")
P7Q = Path("phase7-qa/quality_report.json")
VIDEOS = Path("phase7-clips")
OUT = Path("phase10-publishing-package")

p9 = json.loads(P9.read_text(encoding="utf-8"))
p7 = json.loads(P7.read_text(encoding="utf-8"))
quality = json.loads(P7Q.read_text(encoding="utf-8"))

if p9.get("complete") is not True or p9.get("status") != "pass":
    raise RuntimeError("Phase 9 metadata manifest must be complete/pass.")
if p9.get("sourcePhase") != 8:
    raise RuntimeError("Phase 9 sourcePhase must be 8.")
if not isinstance(p9.get("phase8RunId"), int) or p9["phase8RunId"] <= 0:
    raise RuntimeError("Phase 9 must contain a valid Phase 8 run ID.")
if not isinstance(p9.get("phase7RunId"), int) or p9["phase7RunId"] <= 0:
    raise RuntimeError("Phase 9 must contain a valid Phase 7 run ID.")
if p7.get("complete") is not True or p7.get("phase") != "7_ffmpeg_rendering":
    raise RuntimeError("Phase 7 render manifest is not complete.")
if p7.get("phase6RunId", 0) <= 0:
    raise RuntimeError("Phase 7 manifest lacks a valid Phase 6 provenance ID.")
if len(p9.get("clips", [])) != 2:
    raise RuntimeError("Phase 10 requires exactly two metadata clips.")
if len(quality) != 2 or len(p7.get("plans", [])) != 2:
    raise RuntimeError("Phase 7 must contain exactly two plans and two quality reports.")
if p7.get("originalAudioPreserved") is not True or p7.get("campaignBrandingApplied") is not True:
    raise RuntimeError("Phase 7 audio/branding gates are not satisfied.")

if not VIDEOS.is_dir():
    raise RuntimeError("Phase 7 clip artifact directory is missing.")

qa_by_file = {x["file"]: x for x in quality}
plan_by_file = {x["fileName"]: x for x in p7["plans"]}

if len(qa_by_file) != 2 or len(plan_by_file) != 2:
    raise RuntimeError("Phase 7 provenance indexes are not exactly two unique entries.")

if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "videos").mkdir(parents=True)
(OUT / "metadata").mkdir()
(OUT / "platform").mkdir()

packages = []
for item in sorted(p9["clips"], key=lambda x: x["clipNumber"]):
    filename = item["file"]
    source = VIDEOS / filename
    if not source.is_file() or source.stat().st_size < 100000:
        raise RuntimeError(f"{filename}: rendered video is missing or implausibly small.")
    if filename not in qa_by_file:
        raise RuntimeError(f"{filename}: missing Phase 7 quality report.")
    if filename not in plan_by_file:
        raise RuntimeError(f"{filename}: missing Phase 7 plan provenance.")

    q = qa_by_file[filename]
    plan = plan_by_file[filename]
    duration = float(item["durationSeconds"])
    q_duration = float(q["duration"])
    if abs(duration - q_duration) > 0.15:
        raise RuntimeError(f"{filename}: Phase 9 duration disagrees with Phase 7 QA.")
    if abs(duration - float(plan["durationSeconds"])) > 0.25:
        raise RuntimeError(f"{filename}: Phase 9 duration disagrees with Phase 6/7 plan.")
    if not (10.0 <= duration <= 60.0):
        raise RuntimeError(f"{filename}: duration outside publishing bounds.")
    if int(q["width"]) != 1080 or int(q["height"]) != 1920:
        raise RuntimeError(f"{filename}: not 1080x1920.")
    if q["videoCodec"] != "h264" or int(q["bitrate"]) < 500000 or q.get("hasAudio") is not True:
        raise RuntimeError(f"{filename}: Phase 7 quality contract failed.")

    for platform in ("youtubeShorts", "tiktok", "instagram"):
        obj = item[platform]
        text = obj.get("description", obj.get("caption", ""))
        if not text.startswith("#Ad\n"):
            raise RuntimeError(f"{filename}: #Ad is not the first separate line for {platform}.")
        if obj.get("requiredAccountTag") != "@Callofduty" or "@Callofduty" not in text:
            raise RuntimeError(f"{filename}: @Callofduty requirement failed for {platform}.")
        if len(obj.get("hashtags", [])) > 3:
            raise RuntimeError(f"{filename}: hashtag limit exceeded for {platform}.")

    shutil.copy2(source, OUT / "videos" / filename)
    metadata_source = Path("phase9-metadata") / filename.replace(".mp4", ".json")
    if not metadata_source.is_file():
        raise RuntimeError(f"{filename}: per-clip Phase 9 metadata file is missing.")
    shutil.copy2(metadata_source, OUT / "metadata" / metadata_source.name)

    clip_dir = OUT / "platform" / Path(filename).stem
    clip_dir.mkdir()
    for platform in ("youtubeShorts", "tiktok", "instagram"):
        (clip_dir / f"{platform}.json").write_text(
            json.dumps(item[platform], indent=2, ensure_ascii=False), encoding="utf-8"
        )

    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    packages.append({
        "clipNumber": item["clipNumber"],
        "file": filename,
        "durationSeconds": duration,
        "sha256": sha,
        "phase6PlanId": plan["planId"],
        "phase7Quality": q,
        "platforms": ["youtubeShorts", "tiktok", "instagram"],
        "metadataFile": f"metadata/{Path(filename).stem}.json",
    })

manifest = {
    "schemaVersion": "1.0",
    "phase": "10_publishing_package",
    "complete": True,
    "status": "ready_for_platform_publishing",
    "sourcePhase": 9,
    "phase9RunId": None,
    "phase8RunId": p9["phase8RunId"],
    "phase7RunId": p9["phase7RunId"],
    "phase6RunId": p7["phase6RunId"],
    "campaignId": p9["campaignId"],
    "campaignName": p9["campaignName"],
    "publishingPolicy": {
        "platforms": ["youtubeShorts", "tiktok", "instagram"],
        "postLiveMinimumDays": 30,
        "visibleLikesRequired": True,
        "paidBoostingForbidden": True,
        "storyPostsForbidden": True,
        "duplicatePostingForbidden": True,
        "officialSourceOnly": True,
        "originalAudioPreserved": True,
        "campaignBrandingApplied": True,
        "phase10DoesNotPublish": True,
    },
    "packageChecks": {
        "exactlyTwoClips": True,
        "phase7QualityVerified": True,
        "phase9MetadataVerified": True,
        "platformMetadataSeparated": True,
        "videoChecksumsRecorded": True,
        "provenanceChainComplete": True,
    },
    "clips": packages,
}

(OUT / "publish-manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
)
print("PHASE10_PUBLISHING_PACKAGE_PASS")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
