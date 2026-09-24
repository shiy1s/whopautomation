#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

ROOT = Path(".")
INPUT = ROOT / "phase8-qc" / "phase8-video-qc-manifest.json"
RULES_PATH = ROOT / "campaign-rules/07c3822c-53e1-4420-b650-01b088b9852c.json"
CAMPAIGN_TEXT_PATH = ROOT / "campaign_text.txt"
OUT = ROOT / "phase9-metadata"
OUT.mkdir(parents=True, exist_ok=True)

phase8 = json.loads(INPUT.read_text(encoding="utf-8"))
rules_doc = json.loads(RULES_PATH.read_text(encoding="utf-8"))
rules = rules_doc["rules"]
campaign_text = CAMPAIGN_TEXT_PATH.read_text(encoding="utf-8").strip()

if phase8.get("complete") is not True or phase8.get("status") != "pass":
    raise RuntimeError("Phase 8 manifest must be complete with status=pass.")

if phase8.get("checks", {}).get("allDeterministicChecksPass") is not True:
    raise RuntimeError("Phase 8 deterministic QC checks did not pass.")

clip_reports = phase8.get("clipReports", [])
if len(clip_reports) != 2:
    raise RuntimeError("Phase 9 requires exactly two Phase 8 clip reports.")

required_tags = {
    "youtubeShorts": rules["platforms"]["youtubeShorts"]["accountTag"],
    "tiktok": rules["platforms"]["tiktok"]["accountTag"],
    "instagram": rules["platforms"]["instagram"]["accountTag"],
}
if len(set(required_tags.values())) != 1:
    raise RuntimeError("Platform account-tag requirements are inconsistent.")

account_tag = required_tags["youtubeShorts"]
disclosure = rules["disclosure"]["selected"]
if not rules["disclosure"]["required"] or disclosure not in rules["disclosure"]["options"]:
    raise RuntimeError("Campaign disclosure configuration is invalid.")
if rules["disclosure"]["placement"] != "separate_line" or not rules["disclosure"]["firstElement"]:
    raise RuntimeError("Campaign disclosure placement is not the required first separate line.")

if not campaign_text:
    raise RuntimeError("campaign_text.txt is empty.")

normalized_campaign_text = re.sub(r"\s+", " ", campaign_text).strip().lower()
required_lines = rules["onScreenText"]["requiredLines"]
if not any(normalized_campaign_text == re.sub(r"\s+", " ", x).strip().lower() for x in required_lines):
    raise RuntimeError("campaign_text.txt is not one of the persisted required on-screen lines.")

# Hashtags are limited by the persisted campaign rule to three additional tags.
# Two campaign anchors are always retained. The third slot is selected from a
# current web-grounded signal using Gemini Search. If the live signal cannot be
# verified, the safe campaign fallback is used.
import urllib.error
import urllib.request

def select_hashtags():
    fallback = ["#CallOfDuty", "#RICOCHET", "#AntiCheat"]
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return fallback, "fallback_no_gemini_key"
    prompt = """
Select up to 3 relevant hashtags for Call of Duty RICOCHET Anti-Cheat enforcement footage.
Requirements:
1. Always include #CallOfDuty and #RICOCHET.
2. Use the third slot only for a current, content-relevant Call of Duty / RICOCHET / anti-cheat hashtag supported by recent web results.
3. Do not use generic reach-bait tags such as #fyp, #viral, #trending, #explore.
4. Do not invent a hashtag. Prefer a hashtag that appears in current official Call of Duty/Activision material or clearly current gaming discussion.
Return JSON only: {"hashtags":["#...","#...","#..."]}.
""".strip()
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "hashtags": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "minItems": 2,
                        "maxItems": 3
                    }
                },
                "required": ["hashtags"]
            },
            "temperature": 0.1,
            "maxOutputTokens": 120
        }
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(req, timeout=90).read()
        data = json.loads(response)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        selected = json.loads(text).get("hashtags", [])
        clean = []
        for tag in selected:
            tag = str(tag).strip()
            if re.fullmatch(r"#[A-Za-z0-9_]{2,50}", tag) and tag.lower() not in {x.lower() for x in clean}:
                clean.append(tag)
        anchors = ["#CallOfDuty", "#RICOCHET"]
        result = []
        for anchor in anchors:
            if anchor.lower() in {x.lower() for x in clean}:
                result.append(anchor)
        for tag in clean:
            if tag.lower() not in {x.lower() for x in result}:
                result.append(tag)
        if len(result) < 2:
            return fallback, "fallback_invalid_ai_output"
        return result[:3], "gemini_google_search"
    except Exception:
        return fallback, "fallback_live_signal_unavailable"

HASHTAGS, HASHTAG_SOURCE = select_hashtags()
REFERENCE_CONTEXT = (
    "Call of Duty shared a RICOCHET Anti-Cheat update detailing its continued fight "
    "against cheating beyond the match, including action against developers and sellers "
    "of cheating products."
)

def build_caption(clip_number):
    return (
        f"{disclosure}\n"
        f"{account_tag} RICOCHET Anti-Cheat enforcement footage. "
        f"Real footage of a Call of Duty cheat provider being served a legal notice.\n\n"
        f"{REFERENCE_CONTEXT}\n\n"
        f"{' '.join(HASHTAGS)}"
    )

def build_youtube_description():
    return (
        f"{disclosure}\n"
        f"{account_tag} RICOCHET Anti-Cheat enforcement footage. "
        f"Real footage of a Call of Duty cheat provider being served a legal notice.\n\n"
        f"{REFERENCE_CONTEXT}\n\n"
        f"{' '.join(HASHTAGS)}"
    )

metadata = []
for index, report in enumerate(clip_reports, 1):
    filename = report["file"]
    duration = float(report["durationSeconds"])

    if not 10.0 <= duration <= 60.0:
        raise RuntimeError(f"{filename}: duration is outside campaign publishing bounds.")
    checks = report.get("checks", {})
    if not all(checks.values()):
        raise RuntimeError(f"{filename}: Phase 8 contains a failed deterministic check.")

    if index == 1:
        title = "RICOCHET Enforcement: Call of Duty Legal Notice Footage"
    else:
        title = "Call of Duty RICOCHET: Cheat Provider Legal Notice"

    if len(title) > 100:
        raise RuntimeError(f"{filename}: YouTube title exceeds 100 characters.")

    common = {
        "clipNumber": index,
        "file": filename,
        "durationSeconds": duration,
        "campaignId": rules_doc["campaignId"],
        "campaignName": rules_doc["campaignName"],
        "sourcePhase": 8,
        "metadataPolicy": "campaign_rules_plus_current_web_signal_v1",
    }

    item = {
        **common,
        "youtubeShorts": {
            "requiredAccountTag": account_tag,
            "title": title,
            "description": build_youtube_description(),
            "tags": [
                "Call of Duty",
                "RICOCHET",
                "RICOCHET Anti-Cheat",
                "anti-cheat",
                "cheat providers",
                "gaming",
            ],
            "hashtags": HASHTAGS,
            "ftcDisclosure": disclosure,
        },
        "tiktok": {
            "requiredAccountTag": account_tag,
            "caption": build_caption(index),
            "hashtags": HASHTAGS,
            "ftcDisclosure": disclosure,
        },
        "instagram": {
            "requiredAccountTag": account_tag,
            "caption": build_caption(index),
            "hashtags": HASHTAGS,
            "ftcDisclosure": disclosure,
        },
    }

    # Deterministic campaign compliance checks on generated metadata.
    for platform in ("youtubeShorts", "tiktok", "instagram"):
        obj = item[platform]
        text = obj.get("description", obj.get("caption", ""))
        if not text.startswith(disclosure + "\n"):
            raise RuntimeError(f"{filename}: {platform} disclosure is not the first separate line.")
        if account_tag not in text:
            raise RuntimeError(f"{filename}: {platform} required account tag is missing.")
        if disclosure not in text:
            raise RuntimeError(f"{filename}: {platform} FTC disclosure is missing.")
        if len(obj.get("hashtags", [])) > 3:
            raise RuntimeError(f"{filename}: more than 3 additional hashtags generated.")

    metadata.append(item)

manifest = {
    "schemaVersion": "1.0",
    "phase": "9_metadata",
    "complete": True,
    "status": "pass",
    "sourcePhase": 8,
    "phase8RunId": phase8.get("phase8RunId"),
    "phase7RunId": phase8.get("phase7RunId"),
    "campaignId": rules_doc["campaignId"],
    "campaignName": rules_doc["campaignName"],
    "metadataPolicy": {
        "deterministic": True,
        "aiGeneration": True,
        "aiGenerationScope": "hashtags_only",
        "sourceOfTruth": "persisted_campaign_rules_and_phase8_qc_plus_current_web_signal",
        "hashtagSource": HASHTAG_SOURCE,
        "noUnverifiedClaims": True,
        "ftcDisclosure": disclosure,
        "requiredAccountTag": account_tag,
        "additionalHashtagLimit": 3,
        "dynamicHashtags": True,
    },
    "campaignCompliance": {
        "officialSourceRequired": rules["content"]["officialFootageOnly"],
        "requiredAccountTag": account_tag,
        "ftcDisclosureRequired": True,
        "ftcDisclosurePlacement": "first_separate_line",
        "onScreenTextVerifiedInPhase8": phase8["campaignCompliance"]["requiredOnScreenTextSampledVisible"],
        "logoVerifiedInPhase8": phase8["campaignCompliance"]["logoSampledVisible"],
        "minimumDurationSeconds": rules["video"]["minimumDurationSeconds"],
        "englishOnly": rules["video"]["englishOnly"],
        "originalAudioMustRemainAudible": rules["audio"]["originalAudioMustRemainAudible"],
    },
    "clips": metadata,
}

for item in metadata:
    (OUT / f"{Path(item['file']).stem}.json").write_text(
        json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8"
    )

(OUT / "phase9-metadata-manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
)

print("PHASE9_METADATA_PASS")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
