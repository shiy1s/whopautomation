import json
from pathlib import Path
import time

MIN_DURATION = 10.0
MAX_RENDER_DURATION = 60.0


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def frame_map(analysis):
    return {int(f["frameIndex"]): f for f in analysis["frames"]}


def boundary_from_evidence(frames, first_idx, last_idx, duration):
    by_idx = frame_map({"frames": frames})
    first = by_idx[first_idx]
    last_frame = by_idx[last_idx]

    if first_idx > 1 and (first_idx - 1) in by_idx:
        prev = by_idx[first_idx - 1]
        start = round((float(prev["timestampSeconds"]) + float(first["timestampSeconds"])) / 2, 3)
        start_basis = {"method": "midpoint_between_previous_and_first_evidence_frame"}
    else:
        start = 0.0
        start_basis = {"method": "source_start_before_first_evidence_frame"}

    if last_idx < len(frames) and (last_idx + 1) in by_idx:
        nxt = by_idx[last_idx + 1]
        end = round((float(last_frame["timestampSeconds"]) + float(nxt["timestampSeconds"])) / 2, 3)
        end_basis = {"method": "midpoint_between_last_evidence_and_next_frame"}
    else:
        end = round(float(duration), 3)
        end_basis = {"method": "source_end_after_last_evidence_frame"}

    start = max(0.0, min(start, float(duration)))
    end = max(start, min(end, float(duration)))
    return start, end, start_basis, end_basis


def main():
    phase5 = load("phase5-source/phase5-selection-manifest.json")
    if phase5.get("complete") is not True:
        raise RuntimeError("Phase 5 manifest is not complete=true.")

    selections = phase5.get("selections", [])
    if not selections:
        raise RuntimeError("Phase 5 returned no clip opportunities.")

    analyses_root = Path("phase4b-source/phase4-analysis")
    phase4_manifest = load(analyses_root / "phase4-analysis-manifest.json")
    if phase4_manifest.get("complete") is not True:
        raise RuntimeError("Phase 4B manifest is not complete=true.")

    analyses = {}
    for entry in phase4_manifest.get("assets", []):
        data = load(analyses_root / entry["analysisFile"])
        analyses[data["assetId"]] = data

    if not analyses:
        raise RuntimeError("No Phase 4B asset analyses available.")

    campaign_id = str(phase5.get("campaign", {}).get("campaignId") or "").strip()
    if not campaign_id:
        raise RuntimeError("Phase 5 manifest does not identify campaignId.")
    rules = load(str(Path("campaign-rules") / f"{campaign_id}.json"))
    campaign_rules = rules["rules"]
    campaign_min = max(MIN_DURATION, float(campaign_rules.get("video", {}).get("minimumDurationSeconds") or MIN_DURATION))

    plans = []
    for index, selection in enumerate(selections, 1):
        raw_segments = selection.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            raise RuntimeError(f"Phase 5 clip opportunity {index} has no segments.")

        planned_segments = []
        total = 0.0
        seen_candidate_ids = set()
        seen_asset_windows = {}

        for seg in raw_segments:
            aid = str(seg.get("assetId") or "").strip()
            candidate_id = str(seg.get("candidateId") or "").strip()
            if not aid or not candidate_id:
                raise RuntimeError(f"Phase 5 clip opportunity {index} contains an invalid segment.")
            if candidate_id in seen_candidate_ids:
                raise RuntimeError(f"Duplicate candidate {candidate_id} inside clip opportunity {index}.")
            seen_candidate_ids.add(candidate_id)
            if aid not in analyses:
                raise RuntimeError(f"No Phase 4B analysis for selected asset {aid}.")

            analysis = analyses[aid]
            frames = analysis["frames"]
            first_idx = int(seg["anchorFrameStart"])
            last_idx = int(seg["anchorFrameEnd"])
            if first_idx < 1 or last_idx > len(frames) or first_idx > last_idx:
                raise RuntimeError(f"Invalid evidence frame range for {candidate_id}.")

            start, end, start_basis, end_basis = boundary_from_evidence(
                frames, first_idx, last_idx, float(analysis["durationSeconds"])
            )

            # Preserve the evidence-window contract: Phase 6 may refine only inside
            # the Phase 5 candidate window.
            candidate = next(
                x for x in phase5.get("selections", [])
                for x in x.get("segments", [])
                if x.get("candidateId") == candidate_id
            )
            start = max(start, float(candidate["startSeconds"]))
            end = min(end, float(candidate["endSeconds"]))
            if end <= start:
                raise RuntimeError(f"Phase 6 has no valid evidence window for {candidate_id}.")

            for old_start, old_end in seen_asset_windows.get(aid, []):
                if start < old_end - 0.001 and end > old_start + 0.001:
                    raise RuntimeError(f"Overlapping source segments for asset {aid} in clip {index}.")
            seen_asset_windows.setdefault(aid, []).append((start, end))

            duration = max(0.0, end - start)
            total += duration
            planned_segments.append({
                "candidateId": candidate_id,
                "assetId": aid,
                "fileName": str(seg.get("fileName") or analysis.get("fileName") or ""),
                "role": str(seg.get("role") or "evidence_supported"),
                "sourceDurationSeconds": float(analysis["durationSeconds"]),
                "startSeconds": round(start, 3),
                "endSeconds": round(end, 3),
                "durationSeconds": round(duration, 3),
                "anchorFrameStart": first_idx,
                "anchorFrameEnd": last_idx,
                "anchorTimestampStart": float(candidate["anchorTimestampStart"]),
                "anchorTimestampEnd": float(candidate["anchorTimestampEnd"]),
                "boundaryMethod": "sampled-evidence-midpoint",
                "startBoundaryEvidence": start_basis,
                "endBoundaryEvidence": end_basis,
                "safetyFlags": candidate.get("safetyFlags", []),
            })

        if total < campaign_min:
            raise RuntimeError(f"Clip {index} is below campaign minimum: {total}s < {campaign_min}s.")
        if total > MAX_RENDER_DURATION:
            raise RuntimeError(f"Clip {index} exceeds authoritative renderer ceiling: {total}s > {MAX_RENDER_DURATION}s.")
        if any(x["safetyFlags"] for x in planned_segments):
            raise RuntimeError(f"Clip {index} contains safety-flagged source evidence.")

        plans.append({
            "planId": str(selection.get("planId") or f"CLIP-{index:02d}"),
            "clipNumber": index,
            "segments": planned_segments,
            "durationSeconds": round(total, 3),
            "rationale": str(selection.get("rationale") or "").strip(),
            "confidence": float(selection.get("confidence", 0)),
        })

    output = {
        "schemaVersion": "2.0",
        "phase": "6_clip_planning",
        "complete": True,
        "sourcePhase": {
            "phase": "5_clip_selection",
            "selectionManifest": "phase5-source/phase5-selection-manifest.json",
            "selectedCount": len(selections),
            "phase4bManifest": "phase4b-source/phase4-analysis/phase4-analysis-manifest.json",
            "assetCount": len(analyses),
            "frameCount": sum(len(a.get("frames", [])) for a in analyses.values()),
        },
        "campaign": {
            "campaignId": rules["campaignId"],
            "campaignName": rules["campaignName"],
            "assetSource": rules.get("rules", {}).get("assetSource", {}),
        },
        "planningPolicy": {
            "minimumDurationSeconds": campaign_min,
            "maximumRenderDurationSeconds": MAX_RENDER_DURATION,
            "boundaryMethod": "sampled-evidence-midpoint",
            "audioAnalyzed": False,
            "phase6DoesNotRender": True,
            "phase7RendererRemainsAuthoritative": True,
            "singleAssetAndMultiAssetPlansAllowed": True,
            "unusedAssetsAllowed": True,
            "campaignRulesOverrideComposition": True,
        },
        "renderDirectives": {
            "originalAudioMustRemainAudible": bool(campaign_rules.get("audio", {}).get("originalAudioMustRemainAudible", False)),
            "logoRequired": bool(campaign_rules.get("branding", {}).get("logoRequired", False)),
            "onScreenTextRequired": bool(campaign_rules.get("onScreenText", {}).get("required", False)),
            "onScreenTextOptions": list(campaign_rules.get("onScreenText", {}).get("requiredLines", []) or []),
            "renderAssets": dict(campaign_rules.get("renderAssets", {}) or {}),
            "minimumDurationSeconds": campaign_min,
            "maximumRenderDurationSeconds": MAX_RENDER_DURATION,
        },
        "clipPlanCount": len(plans),
        "clipPlans": plans,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    save(output, "phase6-clip-plan/phase6-clip-plan-manifest.json")
    save({"clipPlans": plans, "campaign": output["campaign"],
          "renderDirectives": output["renderDirectives"]}, "phase6-clip-plan/render-plan.json")
    print("PHASE6_ARTIFACT_VALIDATION_PASS")
    print(f"Clip plans: {len(plans)} | assets available: {len(analyses)}")


if __name__ == "__main__":
    main()
