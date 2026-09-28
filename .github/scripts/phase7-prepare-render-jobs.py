import json
import shutil
from pathlib import Path

MIN_DURATION = 10.0
MAX_DURATION = 60.0

plan_doc = json.loads(Path("phase6-source/render-plan.json").read_text(encoding="utf-8"))
plans = plan_doc.get("clipPlans", [])
if not plans:
    raise RuntimeError("No Phase 6 clip plans found.")

directives = plan_doc.get("renderDirectives", {})
logo_required = bool(directives.get("logoRequired", False))
text_required = bool(directives.get("onScreenTextRequired", False))
text_options = [str(x).strip() for x in directives.get("onScreenTextOptions", []) if str(x).strip()]
logo = Path("Call_of_Duty_Wordmark_Stacked_CMYK_White.png")
campaign_text = Path("campaign_text.txt")

# The current proven campaign has a committed logo/text asset. For a new campaign,
# never reuse it silently: if branding/text is required but not provisioned, stop.
if logo_required and not logo.is_file():
    raise RuntimeError("CAMPAIGN_LOGO_ASSET_REQUIRED_BUT_NOT_PROVISIONED")
if text_required and not text_options and not campaign_text.is_file():
    raise RuntimeError("CAMPAIGN_ONSCREEN_TEXT_REQUIRED_BUT_NOT_PROVISIONED")
if text_required and not text_options and campaign_text.is_file():
    text_options = [campaign_text.read_text(encoding="utf-8").strip()]
if text_required and not text_options:
    raise RuntimeError("CAMPAIGN_ONSCREEN_TEXT_REQUIRED_BUT_EMPTY")

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
    segments = p.get("segments", [])
    if not segments:
        raise RuntimeError(f"Plan {p.get('planId')} has no segments.")

    duration = sum(float(s["endSeconds"]) - float(s["startSeconds"]) for s in segments)
    if not MIN_DURATION <= duration <= MAX_DURATION:
        raise RuntimeError(f"Renderer-incompatible plan {p['planId']}: {duration}s")

    job = root / f"{rank:02d}"
    job.mkdir()
    (job / "work").mkdir()
    (job / "caption_files").mkdir()
    (job / "output").mkdir()

    source_names = {}
    clip_segments = []
    for seg_index, s in enumerate(segments, 1):
        aid = str(s["assetId"])
        source = Path("sources") / f"{aid}.mp4"
        if not source.is_file() or source.stat().st_size < 100000:
            raise RuntimeError(f"Missing real source media for {aid} used by {p['planId']}")

        if aid not in source_names:
            safe_name = f"source_{len(source_names)+1:02d}.mp4"
            shutil.copy2(source, job / safe_name)
            source_names[aid] = safe_name

        clip_segments.append({
            "source": source_names[aid],
            "assetId": aid,
            "start": stamp(float(s["startSeconds"])),
            "end": stamp(float(s["endSeconds"])),
            "purpose": str(s.get("role") or "Phase 6 evidence-bounded segment"),
            "has_burned_in_captions": False,
        })

    clip = {
        "rank": 1,
        "planId": p["planId"],
        "assetIds": list(source_names.keys()),
        "segments": clip_segments,
        "duration_seconds": round(duration, 3),
        "title": f"Campaign Short — Clip {rank}",
        "hook": "Evidence-backed campaign footage.",
        "reason": str(p.get("rationale") or "Phase 5 opportunity-driven selection."),
        "score": round(float(p.get("confidence", 0)) * 100, 2),
    }
    if logo_required:
        (job / logo.name).write_bytes(logo.read_bytes())
    if text_required:
        (job / "campaign_text.txt").write_text(text_options[0], encoding="utf-8")
    (job / "render-config.json").write_text(json.dumps({
        "logoRequired": logo_required,
        "onScreenTextRequired": text_required,
        "onScreenText": text_options[0] if text_required else "",
        "originalAudioMustRemainAudible": bool(directives.get("originalAudioMustRemainAudible", False)),
        "campaignId": plan_doc.get("campaign", {}).get("campaignId"),
        "campaignName": plan_doc.get("campaign", {}).get("campaignName"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    (job / "clips.json").write_text(json.dumps({
        "source_duration_seconds": None,
        "top_moments": [],
        "captions": [],
        "clips": [clip],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

print("PHASE7_RENDER_JOB_INPUTS_PASS")
for rank, p in enumerate(plans, 1):
    print(f"job={rank:02d} plan={p['planId']} segments={len(p.get('segments', []))} duration={p['durationSeconds']}")
