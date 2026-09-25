import json
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

MIN_DURATION = 10.0
MAX_CANDIDATES_PER_ASSET = 6
PAD_SECONDS = 5.0
MIN_RELEVANCE = 0.80
MIN_CLUSTER_FRAMES = 2
MODEL = "gemini-3.5-flash-lite"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def require_key():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return key


def validate_phase4b(manifest, assets):
    if manifest.get("complete") is not True:
        raise ValueError("Phase 4B manifest is not complete=true.")
    if manifest.get("analysis", {}).get("assetCount") != 2:
        raise ValueError("Phase 4B asset count is not 2.")
    if manifest.get("analysis", {}).get("frameCount") != 24:
        raise ValueError("Phase 4B frame count is not 24.")
    if len(assets) != 2:
        raise ValueError("Expected exactly two Phase 4B asset analyses.")

    seen = set()
    for a in assets:
        aid = a.get("assetId")
        if not aid or aid in seen:
            raise ValueError("Duplicate/missing Phase 4B assetId.")
        seen.add(aid)
        frames = a.get("frames")
        if not isinstance(frames, list) or len(frames) != 12:
            raise ValueError(f"Asset {aid} must contain 12 analyses.")
        for f in frames:
            for k in ("frameIndex", "timestampSeconds", "visualDescription", "enforcementSignals", "safetyFlags"):
                if k not in f:
                    raise ValueError(f"Missing {k} in {aid} frame {f.get('frameIndex')}.")
            relevance = f.get("campaignRelevance", f.get("ricochetRelevance"))
            if relevance is None or not 0 <= float(relevance) <= 1:
                raise ValueError("Invalid campaign relevance score.")
            if not isinstance(f["enforcementSignals"], list):
                raise ValueError("enforcementSignals must be a list.")
            if not isinstance(f["safetyFlags"], list):
                raise ValueError("safetyFlags must be a list.")

def make_candidate(asset, frames, start_i, end_i, candidate_no):
    first = frames[start_i]
    last = frames[end_i]
    duration = float(asset["durationSeconds"])
    start = max(0.0, float(first["timestampSeconds"]) - PAD_SECONDS)
    end = min(duration, float(last["timestampSeconds"]) + PAD_SECONDS)

    if end - start < MIN_DURATION:
        need = MIN_DURATION - (end - start)
        start = max(0.0, start - need / 2)
        end = min(duration, end + need / 2)
        if end - start < MIN_DURATION:
            if start == 0:
                end = min(duration, MIN_DURATION)
            else:
                start = max(0.0, end - MIN_DURATION)

    evidence = frames[start_i:end_i + 1]
    relevance = sum(float(x.get("campaignRelevance", x.get("ricochetRelevance", 0))) for x in evidence) / len(evidence)
    signals = []
    for x in evidence:
        for s in x["enforcementSignals"]:
            if s not in signals:
                signals.append(s)

    return {
        "candidateId": f"{asset['assetId'][:8]}-C{candidate_no:02d}",
        "assetId": asset["assetId"],
        "fileName": asset["fileName"],
        "startSeconds": round(start, 3),
        "endSeconds": round(end, 3),
        "durationSeconds": round(end - start, 3),
        "anchorFrameStart": int(first["frameIndex"]),
        "anchorFrameEnd": int(last["frameIndex"]),
        "anchorTimestampStart": float(first["timestampSeconds"]),
        "anchorTimestampEnd": float(last["timestampSeconds"]),
        "evidenceFrameCount": len(evidence),
        "meanRicochetRelevance": round(relevance, 4),
        "enforcementSignals": signals,
        "visualEvidence": [x["visualDescription"] for x in evidence],
        "safetyFlags": sorted({s for x in evidence for s in x["safetyFlags"]}),
    }


def generate_candidates(asset):
    frames = asset["frames"]
    qualifying = [float(f.get("campaignRelevance", f.get("ricochetRelevance", 0))) >= MIN_RELEVANCE for f in frames]
    runs = []
    i = 0
    while i < len(frames):
        if not qualifying[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(frames) and qualifying[j + 1]:
            j += 1
        if (j - i + 1) >= MIN_CLUSTER_FRAMES or i == j:
            runs.append((i, j))
        i = j + 1

    candidates = []
    for idx, (i, j) in enumerate(runs[:MAX_CANDIDATES_PER_ASSET], 1):
        candidates.append(make_candidate(asset, frames, i, j, idx))

    # Always preserve an evidence-backed candidate for the strongest frame if clustering
    # produced no usable result. This is deterministic and still requires AI selection.
    if not candidates:
        strongest = max(range(len(frames)), key=lambda k: float(frames[k].get("campaignRelevance", frames[k].get("ricochetRelevance", 0))))
        candidates.append(make_candidate(asset, frames, strongest, strongest, 1))

    # Deduplicate exact windows.
    unique = []
    seen = set()
    for c in candidates:
        key = (c["startSeconds"], c["endSeconds"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def ai_select(client, campaign_rules, asset, candidates):
    prompt = f"""
You are the Phase 5 clip-selection decision engine for a production video pipeline.

CAMPAIGN:
{campaign_rules.get("campaignName")}

CAMPAIGN RULES:
{json.dumps(campaign_rules.get("rules", campaign_rules), indent=2, ensure_ascii=False)}

ASSET:
{json.dumps({
    "assetId": asset["assetId"],
    "fileName": asset["fileName"],
    "durationSeconds": asset["durationSeconds"]
}, indent=2)}

EVIDENCE-BACKED CANDIDATES:
{json.dumps(candidates, indent=2, ensure_ascii=False)}

Task:
Select the candidate that provides the strongest coherent, campaign-relevant moment for
a short-form edit. This is Phase 5 selection only; do NOT edit, render, caption, or
invent audio/dialogue.

Selection requirements:
- Use only evidence present in the candidate data.
- Prefer a coherent sequence showing the actual campaign topic and required content context.
- Prefer candidates with multiple consecutive evidence frames over isolated evidence.
- Respect the minimum 10-second video requirement.
- Reject candidates with safetyFlags indicating prohibited campaign content.
- Do not claim that audio was heard; Phase 4B is frame-only.
- Do not invent exact event boundaries. The supplied start/end are evidence-window
  boundaries and Phase 6 may refine them.
- Return exactly one selected candidate from the supplied list.

Return ONLY JSON:
{{
  "selectedCandidateId": "...",
  "selectionRationale": "short evidence-based explanation",
  "confidence": 0.0
}}
"""

    for attempt in range(1, 3):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads((response.text or "").strip())
            ids = {c["candidateId"] for c in candidates}
            if data.get("selectedCandidateId") not in ids:
                raise ValueError("AI selected a candidate outside the supplied set.")
            conf = float(data.get("confidence", -1))
            if not 0 <= conf <= 1:
                raise ValueError("AI confidence must be between 0 and 1.")
            rationale = str(data.get("selectionRationale", "")).strip()
            if not rationale:
                raise ValueError("AI selection rationale is empty.")
            return data
        except Exception as exc:
            print(f"Phase 5 AI attempt {attempt} failed: {exc}")
            if attempt == 2:
                raise
            time.sleep(5)


def main():
    root = Path("phase4-source/phase4-analysis")
    manifest_path = root / "phase4-analysis-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Missing exact Phase 4B analysis manifest.")

    manifest = load(manifest_path)
    assets = []
    for entry in manifest["assets"]:
        p = root / entry["analysisFile"]
        if not p.is_file():
            raise RuntimeError(f"Missing Phase 4B analysis file: {p}")
        assets.append(load(p))

    validate_phase4b(manifest, assets)

    campaign_id = str(manifest.get("campaignRules", {}).get("campaignId") or "").strip()\n    if not campaign_id:\n        campaign_id = str(manifest.get("campaign", {}).get("campaignId") or "").strip()\n    if not campaign_id:\n        raise RuntimeError("Phase 4B manifest does not identify campaignId.")\n    rules = load(str(Path("campaign-rules") / f"{campaign_id}.json"))
    client = genai.Client(api_key=require_key())

    selections = []
    candidate_index = []
    for asset in assets:
        candidates = generate_candidates(asset)
        for c in candidates:
            if c["durationSeconds"] < MIN_DURATION:
                raise RuntimeError(f"Generated sub-10s candidate: {c['candidateId']}")
            if c["safetyFlags"]:
                continue
            candidate_index.append(c)

        eligible = [c for c in candidates if not c["safetyFlags"] and c["durationSeconds"] >= MIN_DURATION]
        if not eligible:
            raise RuntimeError(f"No eligible evidence-backed candidates for {asset['assetId']}.")

        decision = ai_select(client, rules, asset, eligible)
        chosen = next(c for c in eligible if c["candidateId"] == decision["selectedCandidateId"])

        selections.append({
            "assetId": asset["assetId"],
            "fileName": asset["fileName"],
            "selectedCandidate": chosen,
            "aiDecision": {
                "model": MODEL,
                "confidence": float(decision["confidence"]),
                "rationale": decision["selectionRationale"],
            },
        })

    if len(selections) != 2:
        raise RuntimeError("Phase 5 must produce exactly two asset selections.")

    output = {
        "schemaVersion": "1.0",
        "phase": "5_clip_selection",
        "complete": True,
        "sourcePhase": {
            "phase": "4B_ai_semantic_analysis",
            "analysisManifest": "phase4-source/phase4-analysis/phase4-analysis-manifest.json",
            "assetCount": 2,
            "frameCount": 24,
        },
        "campaign": {
            "campaignId": rules["campaignId"],
            "campaignName": rules["campaignName"],
        },
        "selectionPolicy": {
            "minimumDurationSeconds": MIN_DURATION,
            "selectionIsEvidenceWindow": True,
            "audioAnalyzed": False,
            "finalBoundaryRefinementPhase": "6_clip_planning",
        },
        "candidateCount": len(candidate_index),
        "selectedCount": len(selections),
        "selections": selections,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    save(output, "phase5-selection/phase5-selection-manifest.json")
    save({"candidates": candidate_index}, "phase5-selection/candidate-index.json")
    print("PHASE5_ARTIFACT_VALIDATION_PASS")
    print(f"Assets selected: {len(selections)} | Candidates: {len(candidate_index)}")


if __name__ == "__main__":
    main()
