import json
import hashlib
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

MIN_DURATION = 10.0
MAX_DURATION = 60.0
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
    expected_assets = int(manifest.get("analysis", {}).get("assetCount", len(assets)) or 0)
    expected_frames = int(manifest.get("analysis", {}).get("frameCount", sum(len(a.get("frames", [])) for a in assets)) or 0)
    if expected_assets != len(assets) or expected_assets < 1:
        raise ValueError(f"Phase 4B asset count contract failed: manifest={expected_assets}, files={len(assets)}.")
    if expected_frames != sum(len(a.get("frames", [])) for a in assets) or expected_frames < 1:
        raise ValueError("Phase 4B frame count contract failed.")
    seen = set()
    for a in assets:
        aid = a.get("assetId")
        if not aid or aid in seen:
            raise ValueError("Duplicate/missing Phase 4B assetId.")
        seen.add(aid)
        frames = a.get("frames")
        if not isinstance(frames, list) or not frames:
            raise ValueError(f"Asset {aid} has no frame analyses.")
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
        "candidateId": f"{hashlib.sha256(asset['assetId'].encode()).hexdigest()[:16]}-C{candidate_no:02d}",
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
        if (j - i + 1) >= MIN_CLUSTER_FRAMES:
            runs.append((i, j))
        i = j + 1

    candidates = []
    for idx, (i, j) in enumerate(runs[:MAX_CANDIDATES_PER_ASSET], 1):
        candidates.append(make_candidate(asset, frames, i, j, idx))

    # No relevant evidence means no candidate. Do not turn the least-irrelevant
    # frame into a clip opportunity merely to keep the pipeline running.

    # Deduplicate exact windows.
    unique = []
    seen = set()
    for c in candidates:
        key = (c["startSeconds"], c["endSeconds"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def ai_plan(client, campaign_rules, candidate_index):
    prompt = f"""
You are the Phase 5 content-planning decision engine for a production short-form video pipeline.

CAMPAIGN:
{campaign_rules.get("campaignName")}

CAMPAIGN RULES ARE THE HIGHEST PRIORITY:
{json.dumps(campaign_rules.get("rules", campaign_rules), indent=2, ensure_ascii=False)}

EVIDENCE-BACKED CANDIDATES FROM ALL DISCOVERED ASSETS:
{json.dumps(candidate_index, indent=2, ensure_ascii=False)}

Your job is to identify only genuinely useful clip opportunities.

IMPORTANT:
- Do NOT assume that assets have an intro, outro, reaction, setup, CTA, or any other fixed role.
- Those are possible roles only when the supplied visual evidence supports them.
- An asset with no strong/useful moment may be completely unused.
- A single clip may contain one segment or multiple ordered segments from different assets.
- A multi-asset clip is allowed only when the segments form a coherent sequence and materially improve the edit.
- A strong standalone moment should remain standalone when adding another asset would weaken coherence.
- Reaction moments may be used before or after a main moment when the evidence supports that story order.
- Possible structures include standalone moment, setup→payoff, reaction→moment, moment→reaction, context→moment, moment→outro-like ending, or other evidence-supported structures.
- Never force a structure merely because it is common.
- Never invent footage, dialogue, audio, event boundaries, or asset relationships.
- Use only candidate IDs supplied above.
- Do not select any candidate with safetyFlags.
- Respect every persisted campaign rule above. If a candidate or combination conflicts with a campaign rule, reject it.
- Respect the campaign's source/footage restrictions, content restrictions, duration rules, language rules, branding rules, and any other persisted requirements.
- Phase 4B is frame-based; do not claim audio was heard.
- Total final duration of each proposed clip must be at least the campaign minimum and no more than 60 seconds because 60s is the authoritative renderer ceiling.
- Do not create duplicate or near-duplicate clip plans.
- Do not select more than one overlapping segment from the same asset in a single clip.
- Prefer fewer strong clips over many weak clips.
- It is valid to return only one clip if that is the only strong opportunity.
- It is valid to leave assets unused.
- Do not exceed 10 clip plans; this is only a safety ceiling, not a campaign rule.

For each selected segment return its candidateId and a concise role such as:
hook, context, setup, strong_moment, reaction, payoff, transition, ending, or other evidence-supported role.
The role is descriptive, not a required taxonomy.

Return ONLY JSON:
{{
  "clipPlans": [
    {{
      "segments": [
        {{"candidateId": "...", "role": "..."}}
      ],
      "rationale": "...",
      "confidence": 0.0
    }}
  ]
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
            plans = data.get("clipPlans")
            if not isinstance(plans, list):
                raise ValueError("clipPlans must be a list.")
            if len(plans) > 10:
                raise ValueError("AI returned more than the technical 10-clip safety ceiling.")
            valid_ids = {c["candidateId"] for c in candidate_index}
            seen_plan_keys = set()
            for plan in plans:
                segs = plan.get("segments")
                if not isinstance(segs, list) or not segs:
                    raise ValueError("Each clip plan must contain at least one segment.")
                ids = [str(s.get("candidateId") or "") for s in segs]
                if any(x not in valid_ids for x in ids):
                    raise ValueError("AI selected a candidate outside the supplied evidence set.")
                if len(ids) != len(set(ids)):
                    raise ValueError("Duplicate candidate inside a clip plan.")
                key = tuple(ids)
                if key in seen_plan_keys:
                    raise ValueError("Duplicate clip plan.")
                seen_plan_keys.add(key)
                if not str(plan.get("rationale") or "").strip():
                    raise ValueError("Clip-plan rationale is empty.")
                conf = float(plan.get("confidence", -1))
                if not 0 <= conf <= 1:
                    raise ValueError("Clip-plan confidence must be between 0 and 1.")
            return data
        except Exception as exc:
            print(f"Phase 5 AI planning attempt {attempt} failed: {exc}")
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
    for entry in manifest.get("assets", []):
        p = root / entry["analysisFile"]
        if not p.is_file():
            raise RuntimeError(f"Missing Phase 4B analysis file: {p}")
        assets.append(load(p))

    validate_phase4b(manifest, assets)

    campaign_id = str(manifest.get("campaignRules", {}).get("campaignId") or "").strip()
    if not campaign_id:
        campaign_id = str(manifest.get("campaign", {}).get("campaignId") or "").strip()
    if not campaign_id:
        raise RuntimeError("Phase 4B manifest does not identify campaignId.")
    rules = load(str(Path("campaign-rules") / f"{campaign_id}.json"))
    client = genai.Client(api_key=require_key())

    candidate_index = []
    for asset in assets:
        candidates = generate_candidates(asset)
        eligible = [c for c in candidates if not c["safetyFlags"] and c["durationSeconds"] >= MIN_DURATION]
        for c in eligible:
            candidate_index.append(c)

    if not candidate_index:
        raise RuntimeError("No eligible evidence-backed candidates across any discovered asset.")

    decision = ai_plan(client, rules, candidate_index)
    by_id = {c["candidateId"]: c for c in candidate_index}
    campaign_min = max(MIN_DURATION, float(rules.get("rules", {}).get("video", {}).get("minimumDurationSeconds") or MIN_DURATION))

    plans = []
    used_asset_windows = []
    for idx, plan in enumerate(decision.get("clipPlans", []), 1):
        segments = []
        total = 0.0
        same_asset_windows = {}
        for s in plan["segments"]:
            c = by_id[s["candidateId"]]
            a = c["assetId"]
            start = float(c["startSeconds"])
            end = float(c["endSeconds"])
            for old_start, old_end in same_asset_windows.get(a, []):
                if start < old_end - 0.001 and end > old_start + 0.001:
                    raise RuntimeError(f"Overlapping segments in AI clip plan {idx} for asset {a}.")
            same_asset_windows.setdefault(a, []).append((start, end))
            total += end - start
            segments.append({
                "candidateId": c["candidateId"],
                "assetId": c["assetId"],
                "fileName": c["fileName"],
                "startSeconds": round(start, 3),
                "endSeconds": round(end, 3),
                "durationSeconds": round(end - start, 3),
                "role": str(s.get("role") or "evidence_supported"),
                "anchorFrameStart": c["anchorFrameStart"],
                "anchorFrameEnd": c["anchorFrameEnd"],
                "anchorTimestampStart": c["anchorTimestampStart"],
                "anchorTimestampEnd": c["anchorTimestampEnd"],
                "safetyFlags": c.get("safetyFlags", []),
            })

        if total < campaign_min:
            raise RuntimeError(f"AI clip plan {idx} is below campaign minimum duration: {total}s < {campaign_min}s.")
        if total > MAX_DURATION:
            raise RuntimeError(f"AI clip plan {idx} exceeds renderer maximum duration: {total}s > {MAX_DURATION}s.")
        if any(seg["safetyFlags"] for seg in segments):
            raise RuntimeError(f"AI clip plan {idx} contains a safety-flagged segment.")

        key = tuple((s["assetId"], s["startSeconds"], s["endSeconds"]) for s in segments)
        if key in used_asset_windows:
            raise RuntimeError(f"Duplicate clip plan {idx}.")
        used_asset_windows.append(key)

        plans.append({
            "planId": f"CLIP-{idx:02d}",
            "segments": segments,
            "durationSeconds": round(total, 3),
            "rationale": str(plan["rationale"]).strip(),
            "confidence": float(plan["confidence"]),
        })

    if not plans:
        raise RuntimeError("AI returned no usable clip opportunities.")

    output = {
        "schemaVersion": "2.0",
        "phase": "5_clip_selection",
        "complete": True,
        "sourcePhase": {
            "phase": "4B_ai_semantic_analysis",
            "analysisManifest": "phase4-source/phase4-analysis/phase4-analysis-manifest.json",
            "assetCount": len(assets),
            "frameCount": sum(len(a.get("frames", [])) for a in assets),
        },
        "campaign": {
            "campaignId": rules["campaignId"],
            "campaignName": rules["campaignName"],
        },
        "selectionPolicy": {
            "minimumDurationSeconds": campaign_min,
            "rendererMaximumDurationSeconds": MAX_DURATION,
            "selectionIsEvidenceBacked": True,
            "audioAnalyzed": False,
            "composition": "opportunity_driven_multi_asset",
            "unusedAssetsAllowed": True,
            "singleAssetClipsAllowed": True,
            "multiAssetClipsAllowedWhenCoherent": True,
            "campaignRulesOverrideComposition": True,
        },
        "candidateCount": len(candidate_index),
        "eligibleAssetCount": len({c["assetId"] for c in candidate_index}),
        "selectedCount": len(plans),
        "selections": plans,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    save(output, "phase5-selection/phase5-selection-manifest.json")
    save({"candidates": candidate_index, "clipPlans": plans}, "phase5-selection/candidate-index.json")
    print("PHASE5_ARTIFACT_VALIDATION_PASS")
    print(f"Assets analyzed: {len(assets)} | Eligible candidates: {len(candidate_index)} | Clip plans: {len(plans)}")


if __name__ == "__main__":
    main()
