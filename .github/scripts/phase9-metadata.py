#!/usr/bin/env python3
import json
import math
import re
from pathlib import Path

ROOT=Path(".")
INPUT=ROOT/"phase8-qc"/"phase8-video-qc-manifest.json"
OUT=ROOT/"phase9-metadata"
OUT.mkdir(parents=True,exist_ok=True)

phase8=json.loads(INPUT.read_text(encoding="utf-8"))
if phase8.get("complete") is not True or phase8.get("status")!="pass":
    raise RuntimeError("Phase 8 manifest must be complete with status=pass.")
if phase8.get("checks",{}).get("allDeterministicChecksPass") is not True:
    raise RuntimeError("Phase 8 deterministic QC checks did not pass.")
clip_reports=phase8.get("clipReports",[])
if len(clip_reports)<1:
    raise RuntimeError("Phase 9 requires at least one Phase 8 clip report.")

campaign_id=str(phase8.get("campaignId") or phase8.get("campaign",{}).get("campaignId") or "").strip()
if not campaign_id:
    raise RuntimeError("Phase 8 manifest does not identify campaignId.")
RULES_PATH=ROOT/"campaign-rules"/f"{campaign_id}.json"
if not RULES_PATH.is_file():
    raise RuntimeError(f"Missing campaign rules: {RULES_PATH}")
rules_doc=json.loads(RULES_PATH.read_text(encoding="utf-8"))
rules=rules_doc["rules"]
video_rules=rules.get("video",{})
minimum_duration=float(video_rules.get("minimumDurationSeconds") if video_rules.get("minimumDurationSeconds") is not None else 10)
maximum_duration=float(video_rules.get("maximumDurationSeconds") if video_rules.get("maximumDurationSeconds") is not None else 60)
if not math.isfinite(minimum_duration) or not math.isfinite(maximum_duration) or min(minimum_duration,maximum_duration)<=0:
    raise RuntimeError("Campaign duration limits must be finite positive seconds.")
minimum_duration,maximum_duration=max(10,minimum_duration),min(60,maximum_duration)
if minimum_duration>maximum_duration:
    raise RuntimeError("Campaign duration limits conflict with the renderer bounds.")
campaign_name=str(rules_doc.get("campaignName") or rules.get("campaignName") or "Campaign").strip()
# Never leak another campaign's repository-global text into this package.
caption=rules.get("caption",{})
campaign_text=str(caption.get("contextText") or "").strip()
if caption.get("mustGiveContext") and not campaign_text:
    raise RuntimeError("Campaign requires contextual captions, but caption.contextText is missing.")

platforms=rules.get("platforms",{})
account_tags={
    "youtubeShorts": platforms.get("youtubeShorts",{}).get("accountTag"),
    "instagram": platforms.get("instagram",{}).get("accountTag"),
    "tiktok": platforms.get("tiktok",{}).get("accountTag"),
}
disclosure=rules.get("disclosure",{}).get("selected") if rules.get("disclosure",{}).get("required") else None
if rules.get("disclosure",{}).get("required") and (not isinstance(disclosure,str) or not disclosure.strip()):
    raise RuntimeError("Campaign requires disclosure, but disclosure.selected is missing.")
disclosure=disclosure.strip() if disclosure else None
disclosure_placement=rules.get("disclosure",{}).get("placement") or "first_separate_line"
if disclosure and disclosure_placement not in ("first_separate_line","first_hashtag_after_text"):
    raise RuntimeError("Campaign disclosure placement needs explicit supported normalization.")
HASHTAGS=[]
for tag in caption.get("requiredHashtags",[]):
    if not isinstance(tag,str) or not re.fullmatch(r"#[A-Za-z0-9_]{1,100}",tag):
        raise RuntimeError("Invalid required campaign hashtag.")
    if tag.lower() not in {x.lower() for x in HASHTAGS}: HASHTAGS.append(tag)
forbidden_tags={str(x).lower() for x in caption.get("forbiddenHashtags",[])}
if any(x.lower() in forbidden_tags for x in HASHTAGS) or (disclosure and disclosure.lower() in forbidden_tags):
    raise RuntimeError("Required hashtag/disclosure conflicts with a forbidden hashtag.")
# Do not infer hashtags from arbitrary brief text: it can contain forbidden
# examples, optional tags, source snippets, or disclosure tokens.
if disclosure_placement=="first_hashtag_after_text" and disclosure:
    if HASHTAGS and HASHTAGS[0].lower()!=disclosure.lower():
        raise RuntimeError("Disclosure placement conflicts with required hashtag order.")
    HASHTAGS=[disclosure]+[x for x in HASHTAGS if x.lower()!=disclosure.lower()]
approved_platforms=[p for p in ("youtubeShorts","tiktok","instagram")
                    if platforms.get(p,{}).get("allowed") is True and not platforms.get(p,{}).get("forbidden")]

def clean_title(index):
    base=re.sub(r"\s+"," ",campaign_name).strip()
    title=f"{base} — Short {index}"
    return title[:100]

def body_text(index, platform):
    parts=[]
    if disclosure and disclosure_placement=="first_separate_line": parts.append(disclosure)
    if account_tags[platform]: parts.append(str(account_tags[platform]))
    parts.append(campaign_name)
    if campaign_text: parts.append(campaign_text)
    final_tags=[tag for tag in HASHTAGS if not (disclosure and disclosure_placement=="first_separate_line" and tag.lower()==disclosure.lower())]
    if final_tags: parts.append(" ".join(final_tags))
    return "\n".join(parts)

metadata=[]
for index,report in enumerate(clip_reports,1):
    filename=report["file"]
    duration=float(report["durationSeconds"])
    if not minimum_duration<=duration<=maximum_duration:
        raise RuntimeError(f"{filename}: duration outside publishing bounds.")
    if not report.get("checks") or not all(report["checks"].values()):
        raise RuntimeError(f"{filename}: Phase 8 contains a failed deterministic check.")
    text=body_text(index,"youtubeShorts")
    if disclosure and disclosure_placement=="first_separate_line" and not text.startswith(disclosure+"\n"):
        raise RuntimeError(f"{filename}: disclosure is not first separate line.")
    item={
        "clipNumber":index,
        "file":filename,
        "durationSeconds":duration,
        "campaignId":rules_doc["campaignId"],
        "campaignName":rules_doc["campaignName"],
        "sourcePhase":8,
        "metadataPolicy":"generic_campaign_rules_v2",
        "approvedPlatforms":approved_platforms,
        "youtubeShorts":{
            "requiredAccountTag":account_tags["youtubeShorts"],
            "title":clean_title(index),
            "description":text,
            "tags":[x.lstrip("#") for x in HASHTAGS],
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
            "disclosurePlacement":disclosure_placement if disclosure else None,
            "allowed": "youtubeShorts" in approved_platforms,
        },
        "tiktok":{
            "requiredAccountTag":account_tags["tiktok"],
            "caption":body_text(index,"tiktok"),
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
            "disclosurePlacement":disclosure_placement if disclosure else None,
            "allowed": "tiktok" in approved_platforms,
        },
        "instagram":{
            "requiredAccountTag":account_tags["instagram"],
            "caption":body_text(index,"instagram"),
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
            "disclosurePlacement":disclosure_placement if disclosure else None,
            "allowed": "instagram" in approved_platforms,
        },
    }
    metadata.append(item)

manifest={
    "schemaVersion":"2.0",
    "phase":"9_metadata",
    "complete":True,
    "status":"pass",
    "sourcePhase":8,
    "phase8RunId":phase8.get("phase8RunId"),
    "phase7RunId":phase8.get("phase7RunId"),
    "campaignId":rules_doc["campaignId"],
    "campaignName":rules_doc["campaignName"],
    "metadataPolicy":{
        "deterministic":True,
        "aiGeneration":False,
        "sourceOfTruth":"persisted_campaign_rules_and_phase8_qc",
        "ftcDisclosure":disclosure,
        "requiredAccountTags":account_tags,
        "requiredHashtags":HASHTAGS,
        "approvedPlatforms":approved_platforms,
        "genericCampaignSupport":True,
    },
    "campaignCompliance":{
        "officialSourceRequired":rules.get("content",{}).get("officialFootageOnly",False),
        "requiredAccountTags":account_tags,
        "ftcDisclosureRequired":bool(rules.get("disclosure",{}).get("required")),
        "ftcDisclosurePlacement":rules.get("disclosure",{}).get("placement"),
        "onScreenTextVerifiedInPhase8":phase8.get("campaignCompliance",{}).get("requiredOnScreenTextSampledVisible"),
        "logoVerifiedInPhase8":phase8.get("campaignCompliance",{}).get("logoSampledVisible"),
        "minimumDurationSeconds":minimum_duration,
        "maximumDurationSeconds":maximum_duration,
        "englishOnly":rules.get("video",{}).get("englishOnly",False),
        "originalAudioMustRemainAudible":rules.get("audio",{}).get("originalAudioMustRemainAudible",False),
    },
    "clips":metadata,
}
for item in metadata:
    (OUT/f"{Path(item['file']).stem}.json").write_text(json.dumps(item,indent=2,ensure_ascii=False),encoding="utf-8")
(OUT/"phase9-metadata-manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
print("PHASE9_METADATA_PASS")
print(json.dumps(manifest,indent=2,ensure_ascii=False))
