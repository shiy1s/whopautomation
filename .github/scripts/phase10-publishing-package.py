#!/usr/bin/env python3
import hashlib
import json
import math
import re
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
video_rules = campaign_rules.get("video", {})
minimum_duration = float(video_rules.get("minimumDurationSeconds") if video_rules.get("minimumDurationSeconds") is not None else 10)
maximum_duration = float(video_rules.get("maximumDurationSeconds") if video_rules.get("maximumDurationSeconds") is not None else 60)
if not math.isfinite(minimum_duration) or not math.isfinite(maximum_duration) or min(minimum_duration, maximum_duration) <= 0:
    raise RuntimeError("Campaign duration limits must be finite positive seconds.")
minimum_duration, maximum_duration = max(10, minimum_duration), min(60, maximum_duration)
if minimum_duration > maximum_duration:
    raise RuntimeError("Campaign duration limits conflict with the renderer bounds.")
if p7.get("campaignId") and p7["campaignId"] != campaign_id:
    raise RuntimeError("Phase 7 and Phase 9 campaign identities disagree.")
platform_labels = {"youtubeShorts": "YouTube Shorts", "tiktok": "TikTok", "instagram": "Instagram"}
platform_ids = {"youtubeShorts": "youtube", "tiktok": "tiktok", "instagram": "instagram"}
approved_platforms = [key for key in platform_labels
                      if campaign_rules.get("platforms", {}).get(key, {}).get("allowed") is True
                      and not campaign_rules.get("platforms", {}).get(key, {}).get("forbidden")]
caption_rules = campaign_rules.get("caption", {})
required_hashtags = caption_rules.get("requiredHashtags", [])
forbidden_hashtags = {str(tag).lower() for tag in caption_rules.get("forbiddenHashtags", [])}
disclosure_rules = campaign_rules.get("disclosure", {})
required_disclosure = disclosure_rules.get("selected") if disclosure_rules.get("required") else None
if disclosure_rules.get("required") and not str(required_disclosure or "").strip():
    raise RuntimeError("Campaign requires disclosure, but disclosure.selected is missing.")
disclosure_placement = disclosure_rules.get("placement") or "first_separate_line"
if required_disclosure and disclosure_placement not in ("first_separate_line", "first_hashtag_after_text"):
    raise RuntimeError("Campaign disclosure placement needs explicit supported normalization.")
review_reasons = []
if campaign_rules.get('publishing', {}).get('testOnly'):
    review_reasons.append('Campaign is explicitly restricted to TEST/non-publishing mode.')
if campaign_rules.get("content", {}).get("creatorRequirements"):
    review_reasons.append("Campaign creator eligibility/proof requirements need verified account evidence.")
if campaign_rules.get("extraction", {}).get("unresolvedRequirements"):
    review_reasons.extend(campaign_rules["extraction"]["unresolvedRequirements"])
for needed, reason in [
    (campaign_rules.get("originality", {}).get("originalEditRequired"), "Original editing and campaign relevance require review of the finished clip."),
    (caption_rules.get("captionsRequired") or campaign_rules.get("video", {}).get("captionsRequired"), "Required timed captions need transcript-grounded text and visual review."),
    (caption_rules.get("hookRequired") or campaign_rules.get("video", {}).get("hookRequired"), "Required hook needs evidence-based editorial review."),
    (video_rules.get("englishOnly"), "Spoken language has not been verified by frame-based analysis."),
    (campaign_rules.get("audio", {}).get("originalAudioMustRemainAudible"), "Audio presence does not establish intelligibility or original-content fidelity."),
    (campaign_rules.get("content", {}).get("officialFootageOnly"), "Official source provenance does not establish every semantic campaign restriction."),
]:
    if needed: review_reasons.append(reason)
if campaign_rules.get("audio", {}).get("originalAudioMustRemainAudible") and p7.get("originalAudioPreserved") is not True:
    raise RuntimeError("Required Phase 7 audio gate is not satisfied.")
branding_required = bool(campaign_rules.get("branding", {}).get("logoRequired") or campaign_rules.get("onScreenText", {}).get("required"))
if branding_required and p7.get("campaignBrandingApplied") is not True:
    raise RuntimeError("Required Phase 7 branding gate is not satisfied.")
if campaign_rules.get('branding', {}).get('providedTemplateRequired'):
    template_spec=campaign_rules.get('renderAssets', {}).get('template') or {}
    if p7.get('campaignTemplateApplied') is not True or not template_spec.get('sha256') or p7.get('campaignTemplateSha256') != template_spec['sha256']:
        raise RuntimeError('Required campaign template provenance is missing or mismatched.')

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
    if not minimum_duration <= float(plan["durationSeconds"]) <= maximum_duration:
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
    if not (minimum_duration <= duration <= maximum_duration):
        raise RuntimeError(f"{filename}: duration outside publishing bounds.")
    if int(q["width"]) != 1080 or int(q["height"]) != 1920:
        raise RuntimeError(f"{filename}: not 1080x1920.")
    if q["videoCodec"] != "h264" or int(q["bitrate"]) < 500000:
        raise RuntimeError(f"{filename}: Phase 7 quality contract failed.")
    if campaign_rules.get("audio", {}).get("originalAudioMustRemainAudible") and q.get("hasAudio") is not True:
        raise RuntimeError(f"{filename}: required original audio is missing.")

    for platform in ("youtubeShorts", "tiktok", "instagram"):
        obj = item[platform]
        text = obj.get("description", obj.get("caption", ""))
        required_cta = str(caption_rules.get('requiredCallToAction') or '').strip()
        if required_cta and required_cta not in text:
            raise RuntimeError(f'{filename}: required campaign call to action missing for {platform}.')
        disclosure = obj.get("ftcDisclosure")
        if required_disclosure and disclosure != required_disclosure:
            raise RuntimeError(f"{filename}: required campaign disclosure missing for {platform}.")
        placement = obj.get("disclosurePlacement") or "first_separate_line"
        if required_disclosure and placement != disclosure_placement:
            raise RuntimeError(f"{filename}: disclosure placement disagrees with campaign rules for {platform}.")
        if disclosure and placement == "first_separate_line" and not text.startswith(str(disclosure) + "\n"):
            raise RuntimeError(f"{filename}: required disclosure is not first separate line for {platform}.")
        if disclosure and placement == "first_hashtag_after_text" and (not re.findall(r"#[A-Za-z0-9_]+", text) or re.findall(r"#[A-Za-z0-9_]+", text)[0] != disclosure):
            raise RuntimeError(f"{filename}: disclosure is not the first hashtag for {platform}.")
        required_tag = campaign_rules.get("platforms", {}).get(platform, {}).get("accountTag") or obj.get("requiredAccountTag")
        if required_tag and str(required_tag) not in text:
            raise RuntimeError(f"{filename}: required account tag missing for {platform}.")
        text_hashtags = re.findall(r"#[A-Za-z0-9_]+", text)
        if any(tag.lower() in forbidden_hashtags for tag in text_hashtags):
            raise RuntimeError(f"{filename}: forbidden hashtag present for {platform}.")
        required_order = [tag for tag in text_hashtags if tag in required_hashtags]
        if required_order != required_hashtags:
            raise RuntimeError(f"{filename}: required hashtags missing or out of order for {platform}.")
        context = str(caption_rules.get("contextText") or "").strip()
        if caption_rules.get("mustGiveContext") and (not context or context not in text):
            raise RuntimeError(f"{filename}: required factual caption context missing for {platform}.")

    shutil.copy2(source, OUT / "videos" / filename)

    metadata_source = Path("phase9-metadata") / filename.replace(".mp4", ".json")
    if not metadata_source.is_file():
        raise RuntimeError(f"{filename}: per-clip Phase 9 metadata file is missing.")
    shutil.copy2(metadata_source, OUT / "metadata" / metadata_source.name)

    clip_dir = OUT / "platform" / Path(filename).stem
    clip_dir.mkdir()
    for platform in approved_platforms:
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
        "platforms": approved_platforms,
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
        "platforms": [platform_labels[key] for key in approved_platforms],
        "approvedPlatforms": [platform_ids[key] for key in approved_platforms],
        "creativeReviewRequired": bool(review_reasons),
        "campaignRequirementsVerified": False if review_reasons else True,
        "campaignRequirementReview": {"status": "required" if review_reasons else "not_required", "reasons": review_reasons},
        "minimumDurationSeconds": minimum_duration,
        "maximumDurationSeconds": maximum_duration,
        "postLiveMinimumDays": campaign_rules.get("publishing", {}).get("postLiveMinimumDays") or campaign_rules.get("publishing", {}).get("liveDurationDays"),
        "visibleLikesRequired": campaign_rules.get("publishing", {}).get("visibleLikesRequired"),
        "paidBoostingForbidden": campaign_rules.get("publishing", {}).get("paidBoostingForbidden"),
        "storyPostsForbidden": campaign_rules.get("publishing", {}).get("storyPostsForbidden"),
        # Idempotency is a pipeline policy even when the brief omits it.
        "duplicatePostingForbidden": True,
        "campaignDuplicatePostingForbidden": bool(campaign_rules.get("publishing", {}).get("duplicatePostingForbidden")),
        "officialSourceOnly": campaign_rules.get("content", {}).get("officialFootageOnly"),
        "originalAudioPreserved": bool(p7.get("originalAudioPreserved")),
        "campaignBrandingApplied": bool(p7.get("campaignBrandingApplied")),
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
