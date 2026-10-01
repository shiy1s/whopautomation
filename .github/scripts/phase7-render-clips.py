import json, os, subprocess

with open("clips.json", encoding="utf-8") as f:
    data = json.load(f)

font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
with open("render-config.json", encoding="utf-8") as f:
    render_config = json.load(f)
logo = render_config.get("logoFile")
logo_required = bool(render_config.get("logoRequired", False))
text_required = bool(render_config.get("onScreenTextRequired", False))
require_audio = bool(render_config.get("originalAudioMustRemainAudible", False))
if logo_required and (not logo or not os.path.isfile(logo)):
    raise RuntimeError("Required campaign logo is not provisioned in this render job.")


def sec(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def run(cmd, label):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode:
        print(p.stderr[-6000:])
        raise RuntimeError(f"{label} failed: exit {p.returncode}")
    print(label, "OK")


def wrap(t):
    words = t.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + (1 if cur else 0) + len(w) <= 28:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines[:2])


for clip in data["clips"]:
    idx = clip["rank"]
    segs = clip["segments"]
    if not segs:
        raise RuntimeError(f"Clip {idx} contains no segments.")

    # Each segment explicitly names its source file. The renderer never guesses
    # which asset a segment belongs to.
    source_files = []
    source_index = {}
    for s in segs:
        source = str(s.get("source") or "").strip()
        if not source:
            raise RuntimeError(f"Clip {idx}: segment source is missing.")
        if source not in source_index:
            source_index[source] = len(source_files)
            source_files.append(source)

    total = sum(sec(s["end"]) - sec(s["start"]) for s in segs)
    if not 10.0 <= total <= 60.0:
        raise RuntimeError(f"Clip {idx}: final duration {total}s outside 10-60s renderer bounds.")

    inputs = []
    for source in source_files:
        if not os.path.isfile(source):
            raise RuntimeError(f"Clip {idx}: missing source file {source}.")
        inputs += ["-i", source]

    source_has_audio = {}
    for source in source_files:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", source],
            capture_output=True, text=True
        )
        source_has_audio[source] = bool(probe.stdout.strip())
        if require_audio and not source_has_audio[source]:
            raise RuntimeError(f"Clip {idx}: campaign requires original audio but {source} has none.")

    use_audio = all(source_has_audio.values())
    assembled = f"work/clip_{idx:02d}_assembled.mp4"
    g = []
    segment_refs = []
    for i, s in enumerate(segs):
        source_idx = source_index[s["source"]]
        a, b = sec(s["start"]), sec(s["end"])
        g.append(f"[{source_idx}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}t]")
        if use_audio:
            g.append(f"[{source_idx}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}t]")
            segment_refs.append(f"[v{i}t][a{i}t]")
        else:
            segment_refs.append(f"[v{i}t]")

    if use_audio:
        g.append("".join(segment_refs) + f"concat=n={len(segs)}:v=1:a=1[vout][aout]")
    else:
        g.append("".join(segment_refs) + f"concat=n={len(segs)}:v=1:a=0[vout]")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"] + inputs
    cmd += ["-filter_complex", ";".join(g), "-map", "[vout]"]
    if use_audio:
        cmd += ["-map", "[aout]"]
    cmd += ["-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "160k"] if use_audio else ["-an"]
    cmd += ["-movflags", "+faststart", assembled]
    run(cmd, f"Assemble clip {idx}")

    # Caption timing remains relative to the final assembled timeline.
    rows = []
    cursor = 0.0
    for s in segs:
        sa, sb = sec(s["start"]), sec(s["end"])
        if s.get("has_burned_in_captions", False):
            cursor += sb - sa
            continue
        for cap in data.get("captions", []):
            a = max(sa, cap["start_sec"])
            b = min(sb, cap["end_sec"])
            if b > a and b - a >= 0.12:
                rows.append((cursor + a - sa, cursor + b - sa, wrap(cap["text"])))
        cursor += sb - sa

    rows.sort()
    clean = []
    for r in rows:
        if clean and r[2] == clean[-1][2] and r[0] <= clean[-1][1] + 0.05:
            clean[-1] = (clean[-1][0], max(clean[-1][1], r[1]), r[2])
        else:
            clean.append(r)

    audio = bool(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", assembled],
        capture_output=True, text=True
    ).stdout.strip())

    parts = [
        "[0:v]split=2[bg][fg]",
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=18:2,eq=brightness=-0.35:saturation=0.70[bg0]",
        f"color=c=black@0.35:s=1080x1920:r=30:d={total:.3f}[shade]",
        "[bg0][shade]overlay=0:0[bg1]",
        "[fg]scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[fg0]",
        "[bg1][fg0]overlay=0:656[base]",
    ]
    cur = "[base]"
    if logo_required:
        parts += [
            "[1:v]scale=220:-1[logo]",
            "[base][logo]overlay=(W-w)/2:115[branded]",
        ]
        cur = "[branded]"

    if text_required:
        if not os.path.isfile("campaign_text.txt"):
            raise RuntimeError(f"Clip {idx}: required campaign on-screen text asset is missing.")
        parts.append(cur + "drawtext=fontfile=" + font + ":textfile=campaign_text.txt:fontcolor=white:fontsize=34:line_spacing=10:text_align=center:x=(w-text_w)/2:y=320:shadowcolor=black@0.85:shadowx=2:shadowy=2:fix_bounds=1[v0]")
        cur = "[v0]"
    else:
        parts.append(cur + "null[v0]")
        cur = "[v0]"

    for j, (a, b, text) in enumerate(clean, 1):
        path = os.path.abspath(f"caption_files/clip_{idx:02d}_{j:03d}.txt").replace("\\", "/")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        nxt = f"[vc{j}]"
        parts.append(cur + f"drawtext=fontfile={font}:textfile={path}:fontcolor=white:fontsize=42:borderw=3:bordercolor=black@0.95:box=1:boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y=1080:enable='between(t,{a:.3f},{b:.3f})'" + nxt)
        cur = nxt
    parts.append(cur + "null[outv]")

    final = f"output/clip_{idx:02d}.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", assembled]
    if logo_required:
        cmd += ["-loop", "1", "-i", logo]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[outv]"]
    if audio:
        cmd += ["-map", "0:a?"]
    cmd += ["-t", f"{total:.3f}", "-r", "30", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-threads", "2"]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]
    cmd += ["-movflags", "+faststart", "-shortest", final]
    run(cmd, f"Final render clip {idx}")
