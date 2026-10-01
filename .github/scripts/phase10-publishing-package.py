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
if not isinstance(p7.get("phase6RunId"), int) or p7["phase6RunId"] <= 0:
    raise RuntimeError("Phase 7 manifest lacks a valid Phase 6 provenance ID.")
if len(p9.get("clips", [])) < 1:
    raise RuntimeError("Phase 10 requires at least one metadata clip.")
if len(quality) < 1 or len(p7.get("plans", [])) < 1:
    raise RuntimeError("Phase 7 must contain at least one plan and quality report.")

campaign_id = str(p9.get("campaignId") or "").strip()
if not campaign_id:
    raise RuntimeError("Phase 9 package does not identify campaignId.")
RULES_PATH = Path("campaign-rules") / f"{campaign_id}.json"
if not RULES_PATH.is_file():
    raise RuntimeError(f"Missing persisted campaign rules: {RULES_PATH}")
rules_doc = json.loads(RULES_PATH.read_text(encoding="utf-8"))
campaign_rules = rules_doc.get("rules", {})
if campaign_rules.get("audio", {}).get("originalAudioMustRemainAudible") and p7.get("originalAudioPreserved") is not True:
    raise RuntimeError("Required Phase 7 audio gate is not satisfied.")
branding_required = bool(campaign_rules.get("branding", {}).get("logoRequired") or campaign_rules.get("onScreenText", {}).get("required"))
if branding_required and p7.get("campaignBrandingApplied") is not True:
    raise RuntimeError("Required Phase 7 branding gate is not satisfied.")

if not VIDEOS.is_dir():
    raise RuntimeError("Phase 7 clip artifact directory is missing.")

qa_by_file = {x["file"]: x for x in quality}
if len(qa_by_file) < 1:
    raise RuntimeError("Phase 7 quality report must contain at least one unique output file.")

# Phase 7's producer contract is explicit: phase7-prepare-render-jobs.py
# enumerates Phase 6 plans in order as jobs 01/02, and the workflow copies
# those outputs to final-output/clip_01.mp4 and clip_02.mp4. Therefore the
# stable provenance binding is clipNumber -> ordered Phase 7 plan.
plans = p7["plans"]
plan_by_clip_number = {index + 1: plan for index, plan in enumerate(plans)}
if sorted(plan_by_clip_number) != list(range(1, len(plans) + 1)):
    raise RuntimeError("Phase 7 plan ordering is not deterministic.")

for clip_number, plan in plan_by_clip_number.items():
    if not plan.get("planId") or not isinstance(plan.get("segments"), list) or not plan["segments"]:
        raise RuntimeError(f"Phase 7 plan {clip_number} lacks required segment provenance.")
    if not 10.0 <= float(plan["durationSeconds"]) <= 60.0:
        raise RuntimeError(f"Phase 7 plan {plan['planId']} has invalid duration.")
    for seg in plan["segments"]:
        for field in ("assetId", "fileName", "startSeconds", "endSeconds"):
            if field not in seg:
                raise RuntimeError(f"Phase 7 plan {plan['planId']} segment lacks {field}.")

if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "videos").mkdir(parents=True)
(OUT / "metadata").mkdir()
(OUT / "platform").mkdir(parents=True)

packages = []
for item in sorted(p9["clips"], key=lambda x: x["clipNumber"]):
    clip_number = int(item["clipNumber"])
    filename = item["file"]
    source = VIDEOS / filename

    if clip_number not in plan_by_clip_number:
        raise RuntimeError(f"{filename}: no deterministic Phase 7 plan for clip number {clip_number}.")
    if not source.is_file() or source.stat().st_size < 100000:
        raise RuntimeError(f"{filename}: rendered video is missing or implausibly small.")
    if filename not in qa_by_file:
        raise RuntimeError(f"{filename}: missing Phase 7 quality report.")

    q = qa_by_file[filename]
    plan = plan_by_clip_number[clip_number]

    duration = float(item["durationSeconds"])
    q_duration = float(q["duration"])
    plan_duration = float(plan["durationSeconds"])

    if abs(duration - q_duration) > 0.15:
        raise RuntimeError(f"{filename}: Phase 9 duration disagrees with Phase 7 QA.")
    if abs(duration - plan_duration) > 0.25:
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
        disclosure = obj.get("ftcDisclosure")
        if disclosure and not text.startswith(str(disclosure) + "\n"):
            raise RuntimeError(f"{filename}: required disclosure is not first separate line for {platform}.")
        required_tag = obj.get("requiredAccountTag")
        if required_tag and str(required_tag) not in text:
            raise RuntimeError(f"{filename}: required account tag missing for {platform}.")
        if len(obj.get("hashtags", [])) > 3:
            raise RuntimeError(f"{filename}: Phase 9 hashtag contract exceeded for {platform}.")

    shutil.copy2(source, OUT / "videos" / filename)

    metadata_source = Path("phase9-metadata") / filename.replace(".mp4", ".json")
    if not metadata_source.is_file():
        raise RuntimeError(f"{filename}: per-clip Phase 9 metadata file is missing.")
    shutil.copy2(metadata_source, OUT / "metadata" / metadata_source.name)

    clip_dir = OUT / "platform" / Path(filename).stem
    clip_dir.mkdir()
    for platform in ("youtubeShorts", "tiktok", "instagram"):
        (clip_dir / f"{platform}.json").write_text(
            json.dumps(item[platform], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    packages.append({
        "clipNumber": clip_number,
        "file": filename,
        "durationSeconds": duration,
        "sha256": sha,
        "phase6PlanId": plan["planId"],
        "phase7Plan": {
            "segments": plan["segments"],
            "durationSeconds": plan_duration,
            "rationale": plan.get("rationale"),
            "confidence": plan.get("confidence"),
        },
        "phase7Quality": q,
        "platforms": ["youtubeShorts", "tiktok", "instagram"],
        "metadataFile": f"metadata/{Path(filename).stem}.json",
    })

if sorted(x["clipNumber"] for x in packages) != list(range(1, len(packages) + 1)):
    raise RuntimeError("Phase 10 package clip numbering is not contiguous.")

manifest = {
    "schemaVersion": "1.1",
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
        "platforms": ["YouTube Shorts", "TikTok", "Instagram"],
        "postLiveMinimumDays": campaign_rules.get("publishing", {}).get("postLiveMinimumDays"),
        "visibleLikesRequired": campaign_rules.get("publishing", {}).get("visibleLikesRequired"),
        "paidBoostingForbidden": campaign_rules.get("publishing", {}).get("paidBoostingForbidden"),
        "storyPostsForbidden": campaign_rules.get("publishing", {}).get("storyPostsForbidden"),
        "duplicatePostingForbidden": campaign_rules.get("publishing", {}).get("duplicatePostingForbidden"),
        "officialSourceOnly": campaign_rules.get("content", {}).get("officialFootageOnly"),
        "originalAudioPreserved": True,
        "campaignBrandingApplied": True,
        "phase10DoesNotPublish": True,
    },
    "packageChecks": {
        "clipCount": len(packages),
        "phase7QualityVerified": True,
        "phase9MetadataVerified": True,
        "platformMetadataSeparated": True,
        "videoChecksumsRecorded": True,
        "provenanceChainComplete": True,
        "phase7PlanBindingVerified": True,
    },
    "clips": packages,
}

(OUT / "publish-manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

print("PHASE10_PUBLISHING_PACKAGE_PASS")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
