import json
import os
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types


MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]
MAX_ATTEMPTS_PER_MODEL = 3
RETRY_SECONDS = 5
EXPECTED_ASSETS = 2
FRAMES_PER_ASSET = 12
EXPECTED_FRAMES = 24


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def require_api_key():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
    return key


def validate_input(manifest):
    if manifest.get("complete") is not True:
        raise ValueError("Phase 4 input manifest is not complete=true.")
    results = manifest.get("results")
    if not isinstance(results, list) or len(results) != EXPECTED_ASSETS:
        raise ValueError(f"Expected {EXPECTED_ASSETS} Phase 4 assets.")
    if manifest.get("videoAssetCount") != EXPECTED_ASSETS:
        raise ValueError("Phase 4 videoAssetCount mismatch.")
    if manifest.get("frameCount") != EXPECTED_FRAMES:
        raise ValueError("Phase 4 total frameCount mismatch.")

    seen = set()
    for asset in results:
        aid = asset.get("assetId")
        frames = asset.get("frames")
        if not aid or aid in seen:
            raise ValueError("Duplicate/missing assetId in Phase 4 manifest.")
        seen.add(aid)
        if len(frames or []) != FRAMES_PER_ASSET or asset.get("frameCount") != FRAMES_PER_ASSET:
            raise ValueError(f"Asset {aid} must contain exactly 12 frames.")
        for frame in frames:
            rel = frame.get("filePath")
            if not rel:
                raise ValueError(f"Missing frame path for {aid}.")
            path = Path("phase4-input") / rel
            if not path.is_file():
                raise ValueError(f"Missing real Phase 4 frame: {path}")
            if path.stat().st_size < 1000:
                raise ValueError(f"Frame is too small: {path}")


def normalize_frame_analysis(item, expected):
    if not isinstance(item, dict):
        raise ValueError("Frame analysis item is not an object.")

    required = [
        "frameIndex", "timestampSeconds", "visualDescription",
        "onScreenText", "entities", "context", "campaignRelevance",
        "enforcementSignals", "safetyFlags", "language", "confidence"
    ]
    missing = [k for k in required if k not in item]
    if missing:
        raise ValueError(f"Missing frame fields: {missing}")

    if int(item["frameIndex"]) != int(expected["frameIndex"]):
        raise ValueError(
            f"Frame index mismatch: expected {expected['frameIndex']} got {item['frameIndex']}"
        )

    ts = float(item["timestampSeconds"])
    expected_ts = float(expected["timestampSeconds"])
    if abs(ts - expected_ts) > 0.02:
        raise ValueError(
            f"Timestamp mismatch for frame {expected['frameIndex']}: "
            f"expected {expected_ts} got {ts}"
        )

    if not isinstance(item["visualDescription"], str) or not item["visualDescription"].strip():
        raise ValueError("visualDescription must be non-empty.")

    for key in ["onScreenText", "context", "language"]:
        if not isinstance(item[key], str):
            raise ValueError(f"{key} must be a string.")

    for key in ["entities", "enforcementSignals", "safetyFlags"]:
        if not isinstance(item[key], list):
            raise ValueError(f"{key} must be a list.")

    relevance = float(item["campaignRelevance"])
    confidence = float(item["confidence"])
    if not 0 <= relevance <= 1 or not 0 <= confidence <= 1:
        raise ValueError("Scores must be between 0 and 1.")

    return {
        "frameIndex": int(expected["frameIndex"]),
        "timestampSeconds": expected_ts,
        "visualDescription": item["visualDescription"].strip(),
        "onScreenText": item["onScreenText"].strip(),
        "entities": [str(x) for x in item["entities"]],
        "context": item["context"].strip(),
        "campaignRelevance": relevance,
        "ricochetRelevance": relevance,
        "enforcementSignals": [str(x) for x in item["enforcementSignals"]],
        "safetyFlags": [str(x) for x in item["safetyFlags"]],
        "language": item["language"].strip(),
        "confidence": confidence,
        "audioEvidence": "not_analyzed_in_phase_4b_frame_input"
    }


def analyze_asset(client, asset, campaign_rules):
    frame_records = asset["frames"]
    prompt = f"""
You are the semantic evidence analyst for a production short-form video pipeline.

Analyze ONLY the supplied real frame images. These are representative frames extracted
from an official Call of Duty RICOCHET campaign asset. Do not invent anything that is
not visibly supported by a frame.

CAMPAIGN CONTEXT:
{json.dumps(campaign_rules.get("rules", campaign_rules), indent=2, ensure_ascii=False)}

IMPORTANT:
- This is Phase 4B semantic analysis, NOT clip selection.
- Do not choose winners, rank moments, or propose final clips.
- Do not invent dialogue, audio, events, people, dates, statistics, accusations, or
  off-screen facts.
- A still frame cannot establish spoken dialogue. Do not infer audio content.
- Read visible on-screen text only when actually legible; otherwise use an empty string.
- Identify visible people, objects, settings, documents, logos, bodycam-like footage,
  gameplay, legal-notice/enforcement cues, and other concrete visual evidence.
- "campaign relevance" means visible relevance to the campaign topic, not a guess about
  hidden context.
- Safety flags must be based on visible content only.
- Return exactly one analysis object for every supplied frame, preserving frame order.

The frame metadata is authoritative:
{json.dumps(frame_records, indent=2)}

Return ONLY valid JSON in this exact structure:
{{
  "assetId": "{asset['assetId']}",
  "frames": [
    {{
      "frameIndex": 1,
      "timestampSeconds": 4.82,
      "visualDescription": "what is visibly shown",
      "onScreenText": "legible visible text or empty string",
      "entities": ["visible entity/object 1"],
      "context": "concrete visual context only",
      "ricochetRelevance": 0.0,
      "enforcementSignals": ["visible signal"],
      "safetyFlags": [],
      "language": "English",
      "confidence": 0.0
    }}
  ]
}}

There must be exactly {len(frame_records)} frame objects, one per supplied frame.
"""

    parts = [prompt]
    for frame in frame_records:
        path = Path("phase4-input") / frame["filePath"]
        data = path.read_bytes()
        parts.append(
            types.Part.from_bytes(
                data=data,
                mime_type="image/jpeg",
            )
        )
        parts.append(
            f"FRAME_METADATA: frameIndex={frame['frameIndex']}, "
            f"timestampSeconds={frame['timestampSeconds']}"
        )

    last_error = None
    for model in MODELS:
        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                print(
                    f"Analyzing {asset['fileName']} with {model} "
                    f"(attempt {attempt}/{MAX_ATTEMPTS_PER_MODEL})..."
                )
                response = client.models.generate_content(
                    model=model,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                text = (response.text or "").strip()
                if not text:
                    raise ValueError("Gemini returned an empty response.")
                data = json.loads(text)
                if data.get("assetId") != asset["assetId"]:
                    raise ValueError("Gemini returned the wrong assetId.")
                returned = data.get("frames")
                if not isinstance(returned, list) or len(returned) != len(frame_records):
                    raise ValueError(
                        f"Expected {len(frame_records)} frame analyses, got "
                        f"{len(returned) if isinstance(returned, list) else 'non-list'}."
                    )
                by_index = {int(x.get("frameIndex")): x for x in returned}
                normalized = []
                for expected in frame_records:
                    normalized.append(
                        normalize_frame_analysis(
                            by_index.get(int(expected["frameIndex"])),
                            expected,
                        )
                    )
                return {
                    "model": model,
                    "frames": normalized,
                }
            except Exception as exc:
                last_error = exc
                print(f"Model {model} failed: {exc}")
                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    time.sleep(RETRY_SECONDS)
        print(f"Moving to fallback model after {model} failures.")

    raise RuntimeError(
        f"All Gemini models failed for asset {asset['assetId']}: {last_error}"
    )


def main():
    manifest_path = Path("phase4-input/phase4-input-manifest.json")
    campaign_id = str(manifest.get("campaignId") or "").strip()\n    if not campaign_id:\n        campaign_id = str(manifest.get("campaignRules", {}).get("campaignId") or "").strip()\n    rules_path = Path("campaign-rules") / f"{campaign_id}.json"
    output_dir = Path("phase4-analysis")

    if not manifest_path.is_file():
        raise RuntimeError(f"Missing Phase 4 input manifest: {manifest_path}")
    if not rules_path.is_file():
        raise RuntimeError(f"Missing persisted campaign rules: {rules_path}")

    manifest = load_json(manifest_path)
    campaign_rules = load_json(rules_path)
    validate_input(manifest)
    client = genai.Client(api_key=require_api_key())

    analyses = []
    total = 0
    for asset in manifest["results"]:
        result = analyze_asset(client, asset, campaign_rules)
        asset_output = {
            "assetId": asset["assetId"],
            "fileName": asset["fileName"],
            "durationSeconds": asset["durationSeconds"],
            "width": asset["width"],
            "height": asset["height"],
            "sourceFrameCount": asset["frameCount"],
            "analysisModel": result["model"],
            "audioAnalyzed": False,
            "frames": result["frames"],
        }
        save_json(
            asset_output,
            output_dir / f"{asset['assetId']}.json",
        )
        analyses.append(asset_output)
        total += len(result["frames"])

    if len(analyses) != EXPECTED_ASSETS or total != EXPECTED_FRAMES:
        raise RuntimeError(
            f"Phase 4B completeness failure: assets={len(analyses)} frames={total}"
        )

    # Deterministic cross-check: every Phase 4 frame must have exactly one analysis.
    for source_asset, analyzed_asset in zip(manifest["results"], analyses):
        source_pairs = [
            (int(f["frameIndex"]), float(f["timestampSeconds"]))
            for f in source_asset["frames"]
        ]
        analyzed_pairs = [
            (int(f["frameIndex"]), float(f["timestampSeconds"]))
            for f in analyzed_asset["frames"]
        ]
        if source_pairs != analyzed_pairs:
            raise RuntimeError(
                f"Frame provenance mismatch for {source_asset['assetId']}"
            )

    out_manifest = {
        "schemaVersion": "1.0",
        "phase": "4B_ai_semantic_analysis",
        "complete": True,
        "sourcePhase": {
            "phase": "4A_media_evidence",
            "inputManifest": "phase4-input/phase4-input-manifest.json",
            "videoAssetCount": manifest["videoAssetCount"],
            "frameCount": manifest["frameCount"],
        },
        "campaignRules": {
            "path": str(rules_path),
            "campaignId": campaign_rules.get("campaignId"),
            "campaignName": campaign_rules.get("campaignName"),
        },
        "analysis": {
            "provider": "Google Gemini",
            "modelsUsed": sorted({a["analysisModel"] for a in analyses}),
            "frameBased": True,
            "audioAnalyzed": False,
            "assetCount": len(analyses),
            "frameCount": total,
        },
        "assets": [
            {
                "assetId": a["assetId"],
                "fileName": a["fileName"],
                "analysisFile": f"{a['assetId']}.json",
                "frameCount": len(a["frames"]),
            }
            for a in analyses
        ],
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_json(out_manifest, output_dir / "phase4-analysis-manifest.json")
    print("PHASE4B_ARTIFACT_VALIDATION_PASS")
    print(f"Assets: {len(analyses)} | Frames: {total}")


if __name__ == "__main__":
    main()
