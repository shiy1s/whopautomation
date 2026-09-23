import json
from pathlib import Path

MAX_DURATION = 60.0
MIN_DURATION = 10.0

plan = json.loads(Path("phase6-source/phase6-clip-plan/render-plan.json").read_text(encoding="utf-8"))
if len(plan.get("clipPlans", [])) != 2:
    raise RuntimeError("Phase 7 requires exactly two Phase 6 clip plans.")

clips = []
for rank, p in enumerate(plan["clipPlans"], 1):
    start = float(p["startSeconds"])
    end = float(p["endSeconds"])
    duration = end - start
    if duration < MIN_DURATION or duration > MAX_DURATION:
        raise RuntimeError(f"Renderer-incompatible duration for {p['planId']}: {duration}")
    clips.append({
        "rank": rank,
        "assetId": p["assetId"],
        "source_file": f"sources/{p['assetId']}.mp4",
        "segments": [{
            "start": f"00:00:{start:06.3f}",
            "end": f"00:00:{end:06.3f}",
            "purpose": "Phase 6 evidence-bounded clip plan",
            "has_burned_in_captions": True
        }],
        "duration_seconds": round(duration, 3),
        "title": f"RICOCHET Enforcement — {p['fileName']}",
        "hook": "Real RICOCHET enforcement footage.",
        "reason": "Phase 5 selected candidate with Phase 6 evidence-bounded boundaries.",
        "score": 100,
        "phase6_plan_id": p["planId"],
    })

Path("clips.json").write_text(json.dumps({
    "source_duration_seconds": None,
    "top_moments": [],
    "captions": [],
    "clips": clips
}, indent=2), encoding="utf-8")
print("PHASE7_RENDER_INPUT_VALIDATION_PASS")
for c in clips:
    print(c["rank"], c["assetId"], c["segments"][0]["start"], c["segments"][0]["end"])
