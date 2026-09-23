import json
from pathlib import Path

MIN_DURATION = 10.0


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

    if first_idx > 1:
        prev = by_idx[first_idx - 1]
        start = round((float(prev["timestampSeconds"]) + float(first["timestampSeconds"])) / 2, 3)
        start_basis = {
            "method": "midpoint_between_previous_and_first_evidence_frame",
            "previousFrameIndex": first_idx - 1,
            "previousTimestampSeconds": float(prev["timestampSeconds"]),
            "firstEvidenceFrameIndex": first_idx,
            "firstEvidenceTimestampSeconds": float(first["timestampSeconds"]),
        }
    else:
        start = 0.0
        start_basis = {
            "method": "source_start_before_first_evidence_frame",
            "firstEvidenceFrameIndex": first_idx,
            "firstEvidenceTimestampSeconds": float(first["timestampSeconds"]),
        }

    if last_idx < len(frames):
        nxt = by_idx[last_idx + 1]
        end = round((float(last_frame["timestampSeconds"]) + float(nxt["timestampSeconds"])) / 2, 3)
        end_basis = {
            "method": "midpoint_between_last_evidence_and_next_frame",
            "lastEvidenceFrameIndex": last_idx,
            "lastEvidenceTimestampSeconds": float(last_frame["timestampSeconds"]),
            "nextFrameIndex": last_idx + 1,
            "nextTimestampSeconds": float(nxt["timestampSeconds"]),
        }
    else:
        end = round(float(duration), 3)
        end_basis = {
            "method": "source_end_after_last_evidence_frame",
            "lastEvidenceFrameIndex": last_idx,
            "lastEvidenceTimestampSeconds": float(last_frame["timestampSeconds"]),
        }

    start = max(0.0, min(start, float(duration)))
    end = max(start, min(end, float(duration)))

    if end - start < MIN_DURATION:
        raise ValueError(f"Boundary plan is shorter than {MIN_DURATION}s.")

    return start, end, start_basis, end_basis


def main():
    phase5 = load("phase5-source/phase5-selection-manifest.json")
    if phase5.get("complete") is not True:
        raise RuntimeError("Phase 5 manifest is not complete=true.")
    if phase5.get("selectedCount") != 2 or len(phase5.get("selections", [])) != 2:
        raise RuntimeError("Phase 5 must contain exactly two selections.")

    analyses_root = Path("phase4b-source/phase4-analysis")
    phase4_manifest = load(analyses_root / "phase4-analysis-manifest.json")
    if phase4_manifest.get("complete") is not True:
        raise RuntimeError("Phase 4B manifest is not complete=true.")
    if phase4_manifest.get("analysis", {}).get("assetCount") != 2:
        raise RuntimeError("Phase 4B asset count contract failed.")
    if phase4_manifest.get("analysis", {}).get("frameCount") != 24:
        raise RuntimeError("Phase 4B frame count contract failed.")

    analyses = {}
    for entry in phase4_manifest["assets"]:
        data = load(analyses_root / entry["analysisFile"])
        if len(data.get("frames", [])) != 12:
            raise RuntimeError(f"Phase 4B analysis for {data.get('assetId')} must contain 12 frames.")
        analyses[data["assetId"]] = data

    rules = load("campaign-rules/07c3822c-53e1-4420-b650-01b088b9852c.json")

    plans = []
    for selection in phase5["selections"]:
        candidate = selection["selectedCandidate"]
        aid = candidate["assetId"]
        if aid not in analyses:
            raise RuntimeError(f"No Phase 4B analysis for selected asset {aid}.")

        analysis = analyses[aid]
        frames = analysis["frames"]
        first_idx = int(candidate["anchorFrameStart"])
        last_idx = int(candidate["anchorFrameEnd"])

        if first_idx < 1 or last_idx > len(frames) or first_idx > last_idx:
            raise RuntimeError(f"Invalid evidence frame range for {candidate['candidateId']}.")

        start, end, start_basis, end_basis = boundary_from_evidence(
            frames, first_idx, last_idx, float(analysis["durationSeconds"])
        )

        # Phase 6 is allowed to refine the Phase 5 evidence window, but it must never
        # extend beyond the source media or below the campaign minimum duration.
        if start < candidate["startSeconds"] - 0.001 or end > candidate["endSeconds"] + 0.001:
            raise RuntimeError(
                f"Phase 6 boundary escaped Phase 5 evidence window for {candidate['candidateId']}."
            )

        planned = {
            "planId": f"{aid[:8]}-P01",
            "candidateId": candidate["candidateId"],
            "assetId": aid,
            "fileName": candidate["fileName"],
            "sourceDurationSeconds": float(analysis["durationSeconds"]),
            "startSeconds": start,
            "endSeconds": end,
            "durationSeconds": round(end - start, 3),
            "anchorFrameStart": first_idx,
            "anchorFrameEnd": last_idx,
            "anchorTimestampStart": float(candidate["anchorTimestampStart"]),
            "anchorTimestampEnd": float(candidate["anchorTimestampEnd"]),
            "boundaryMethod": "sampled-evidence-midpoint",
            "startBoundaryEvidence": start_basis,
            "endBoundaryEvidence": end_basis,
            "boundaryPrecision": "bounded_by_phase4b_sampled_frame_evidence",
            "audioAnalyzed": False,
            "safetyFlags": candidate.get("safetyFlags", []),
            "renderDirectives": {
                "originalAudioMustRemainAudible": rules["rules"]["audio"]["originalAudioMustRemainAudible"],
                "logoRequired": rules["rules"]["branding"]["logoRequired"],
                "logoMustRemainVisible": rules["rules"]["branding"]["logoMustRemainVisible"],
                "onScreenTextRequired": rules["rules"]["onScreenText"]["required"],
                "onScreenTextOptions": rules["rules"]["onScreenText"]["requiredLines"],
                "minimumDurationSeconds": MIN_DURATION,
            },
            "phase7Note": "Renderer must use these planned boundaries as inputs; do not rewrite the proven FFmpeg renderer.",
        }
        plans.append(planned)

    if len(plans) != 2:
        raise RuntimeError("Phase 6 must produce exactly two clip plans.")
    if len({p["assetId"] for p in plans}) != 2:
        raise RuntimeError("Phase 6 plans must cover two unique assets.")

    output = {
        "schemaVersion": "1.0",
        "phase": "6_clip_planning",
        "complete": True,
        "sourcePhase": {
            "phase": "5_clip_selection",
            "selectionManifest": "phase5-source/phase5-selection-manifest.json",
            "selectedCount": 2,
            "phase4bManifest": "phase4b-source/phase4-analysis/phase4-analysis-manifest.json",
            "frameCount": 24,
        },
        "campaign": {
            "campaignId": rules["campaignId"],
            "campaignName": rules["campaignName"],
        },
        "planningPolicy": {
            "minimumDurationSeconds": MIN_DURATION,
            "boundaryMethod": "sampled-evidence-midpoint",
            "audioAnalyzed": False,
            "phase6DoesNotRender": True,
            "phase7RendererRemainsAuthoritative": True,
        },
        "clipPlanCount": len(plans),
        "clipPlans": plans,
        "createdAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }

    save(output, "phase6-clip-plan/phase6-clip-plan-manifest.json")
    save({"clipPlans": plans}, "phase6-clip-plan/render-plan.json")
    print("PHASE6_ARTIFACT_VALIDATION_PASS")
    print(f"Clip plans: {len(plans)}")


if __name__ == "__main__":
    main()
