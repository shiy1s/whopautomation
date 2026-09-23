import json
import shutil
from pathlib import Path

MIN_DURATION = 10.0
MAX_DURATION = 60.0

plan = json.loads(Path("phase6-source/phase6-clip-plan/render-plan.json").read_text(encoding="utf-8"))
plans = plan.get("clipPlans", [])
if len(plans) != 2:
    raise RuntimeError("Expected exactly two Phase 6 clip plans.")

logo = Path("Call_of_Duty_Wordmark_Stacked_CMYK_White.png")
campaign_text = Path("campaign_text.txt")
if not logo.is_file() or not campaign_text.is_file():
    raise RuntimeError("Proven renderer campaign assets are missing.")

root = Path("render-jobs")
if root.exists():
    shutil.rmtree(root)
root.mkdir()

def stamp(seconds):
    seconds = float(seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"

for rank, p in enumerate(plans, 1):
    start = float(p["startSeconds"])
    end = float(p["endSeconds"])
    duration = end - start
    if not MIN_DURATION <= duration <= MAX_DURATION:
        raise RuntimeError(f"Renderer-incompatible plan {p['planId']}: {duration}s")
    source = Path("sources") / f"{p['assetId']}.mp4"
    if not source.is_file() or source.stat().st_size < 100000:
        raise RuntimeError(f"Missing real source media for {p['assetId']}")

    job = root / f"{rank:02d}"
    job.mkdir()
    shutil.copy2(source, job / "source.mp4")
    shutil.copy2(logo, job / logo.name)
    shutil.copy2(campaign_text, job / campaign_text.name)

    clip = {
        "rank": 1,
        "assetId": p["assetId"],
        "segments": [{
            "start": stamp(start),
            "end": stamp(end),
            "purpose": "Phase 6 evidence-bounded clip plan",
            "has_burned_in_captions": True
        }],
        "duration_seconds": round(duration, 3),
        "title": f"RICOCHET Enforcement — {p['fileName']}",
        "hook": "Real RICOCHET enforcement footage.",
        "reason": "Phase 5 selection with Phase 6 evidence-bounded boundaries.",
        "score": 100
    }
    (job / "clips.json").write_text(json.dumps({
        "source_duration_seconds": float(p["sourceDurationSeconds"]),
        "top_moments": [],
        "captions": [],
        "clips": [clip]
    }, indent=2, ensure_ascii=False), encoding="utf-8")

print("PHASE7_RENDER_JOB_INPUTS_PASS")
for rank, p in enumerate(plans, 1):
    print(f"job={rank:02d} asset={p['assetId']} start={p['startSeconds']} end={p['endSeconds']} duration={p['durationSeconds']}")
