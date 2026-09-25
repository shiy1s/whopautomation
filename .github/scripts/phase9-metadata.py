#!/usr/bin/env python3
import json
import re
from pathlib import Path

ROOT=Path(".")
INPUT=ROOT/"phase8-qc"/"phase8-video-qc-manifest.json"
CAMPAIGN_TEXT_PATH=ROOT/"campaign_text.txt"
OUT=ROOT/"phase9-metadata"
OUT.mkdir(parents=True,exist_ok=True)

phase8=json.loads(INPUT.read_text(encoding="utf-8"))
if phase8.get("complete") is not True or phase8.get("status")!="pass":
    raise RuntimeError("Phase 8 manifest must be complete with status=pass.")
if phase8.get("checks",{}).get("allDeterministicChecksPass") is not True:
    raise RuntimeError("Phase 8 deterministic QC checks did not pass.")
clip_reports=phase8.get("clipReports",[])
if len(clip_reports)!=2:
    raise RuntimeError("Phase 9 requires exactly two Phase 8 clip reports.")

campaign_id=str(phase8.get("campaignId") or phase8.get("campaign",{}).get("campaignId") or "").strip()
if not campaign_id:
    raise RuntimeError("Phase 8 manifest does not identify campaignId.")
RULES_PATH=ROOT/"campaign-rules"/f"{campaign_id}.json"
if not RULES_PATH.is_file():
    raise RuntimeError(f"Missing campaign rules: {RULES_PATH}")
rules_doc=json.loads(RULES_PATH.read_text(encoding="utf-8"))
rules=rules_doc["rules"]
campaign_name=str(rules_doc.get("campaignName") or rules.get("campaignName") or "Campaign").strip()
campaign_text=CAMPAIGN_TEXT_PATH.read_text(encoding="utf-8").strip() if CAMPAIGN_TEXT_PATH.exists() else ""

platforms=rules.get("platforms",{})
account_tags={
    "youtubeShorts": platforms.get("youtubeShorts",{}).get("accountTag"),
    "instagram": platforms.get("instagram",{}).get("accountTag"),
    "tiktok": platforms.get("tiktok",{}).get("accountTag"),
}
disclosure=rules.get("disclosure",{}).get("selected") if rules.get("disclosure",{}).get("required") else None
raw_text=str(rules.get("rawText") or "")
found_tags=[]
for tag in re.findall(r"#[A-Za-z0-9_]{2,50}",raw_text):
    if tag.lower() not in {x.lower() for x in found_tags}:
        found_tags.append(tag)
if not found_tags:
    slug=re.sub(r"[^A-Za-z0-9]+","",campaign_name)
    if slug:
        found_tags=[("#"+slug[:40])]
if not found_tags:
    found_tags=["#ContentRewards"]
HASHTAGS=found_tags[:3]

def clean_title(index):
    base=re.sub(r"\s+"," ",campaign_name).strip()
    title=f"{base} — Short {index}"
    return title[:100]

def body_text(index):
    parts=[]
    if disclosure: parts.append(disclosure)
    if account_tags["youtubeShorts"]: parts.append(str(account_tags["youtubeShorts"]))
    parts.append(campaign_name)
    if campaign_text: parts.append(campaign_text[:600])
    parts.append(" ".join(HASHTAGS))
    return "\n".join(parts)

metadata=[]
for index,report in enumerate(clip_reports,1):
    filename=report["file"]
    duration=float(report["durationSeconds"])
    if not 10.0<=duration<=60.0:
        raise RuntimeError(f"{filename}: duration outside publishing bounds.")
    if not all(report.get("checks",{}).values()):
        raise RuntimeError(f"{filename}: Phase 8 contains a failed deterministic check.")
    text=body_text(index)
    if disclosure and not text.startswith(disclosure+"\n"):
        raise RuntimeError(f"{filename}: disclosure is not first separate line.")
    item={
        "clipNumber":index,
        "file":filename,
        "durationSeconds":duration,
        "campaignId":rules_doc["campaignId"],
        "campaignName":rules_doc["campaignName"],
        "sourcePhase":8,
        "metadataPolicy":"generic_campaign_rules_v2",
        "youtubeShorts":{
            "requiredAccountTag":account_tags["youtubeShorts"],
            "title":clean_title(index),
            "description":text,
            "tags":[x.lstrip("#") for x in HASHTAGS],
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
        },
        "tiktok":{
            "requiredAccountTag":account_tags["tiktok"],
            "caption":text,
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
        },
        "instagram":{
            "requiredAccountTag":account_tags["instagram"],
            "caption":text,
            "hashtags":HASHTAGS,
            "ftcDisclosure":disclosure,
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
        "additionalHashtagLimit":3,
        "genericCampaignSupport":True,
    },
    "campaignCompliance":{
        "officialSourceRequired":rules.get("content",{}).get("officialFootageOnly",False),
        "requiredAccountTags":account_tags,
        "ftcDisclosureRequired":bool(rules.get("disclosure",{}).get("required")),
        "ftcDisclosurePlacement":rules.get("disclosure",{}).get("placement"),
        "onScreenTextVerifiedInPhase8":phase8.get("campaignCompliance",{}).get("requiredOnScreenTextSampledVisible",True),
        "logoVerifiedInPhase8":phase8.get("campaignCompliance",{}).get("logoSampledVisible",True),
        "minimumDurationSeconds":rules.get("video",{}).get("minimumDurationSeconds") or 10,
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
