#!/usr/bin/env python3
import base64, json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

REPO = os.environ["GITHUB_REPOSITORY"]
ROOT = Path(os.environ.get("PACKAGE_DIR", "phase10-publishing-package"))
PHASE11_RUN_ID = os.environ["PHASE11_RUN_ID"]
LEDGER_PATH = Path("state/phase11-publication-ledger.json")
TRACKING_PATH = Path("state/phase12-publication-tracking.json")

def die(msg):
    raise RuntimeError(msg)

def gh_api(url, method="GET", body=None):
    token = os.environ["GH_TOKEN"]
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    else:
        data = None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"GitHub HTTP {e.code}: {detail}")

def graph_json(url, token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Instagram HTTP {e.code}: {detail}")

def instagram_snapshot(media_id, token):
    version = os.environ.get("INSTAGRAM_GRAPH_VERSION") or "v25.0"
    fields = "id,caption,media_type,media_product_type,timestamp,permalink"
    q = urllib.parse.urlencode({"fields": fields})
    d = graph_json(f"https://graph.instagram.com/{version}/{media_id}?{q}", token)
    if str(d.get("id")) != str(media_id):
        die(f"Instagram media ID mismatch for {media_id}")
    media_type = d.get("media_type")
    if media_type not in ("VIDEO", "REELS"):
        die(f"Instagram media {media_id} is not a video/reel: {media_type}")
    return {
        "mediaId": str(d["id"]),
        "mediaType": media_type,
        "mediaProductType": d.get("media_product_type"),
        "captionPresent": bool(d.get("caption")),
        "publishedAt": d.get("timestamp"),
        "permalink": d.get("permalink"),
        "trackedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

def google_json(url, token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"YouTube HTTP {e.code}: {detail}")

def refresh_youtube_token():
    fields = {
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Google OAuth HTTP {e.code}: {detail}")
    token = d.get("access_token")
    if not token:
        die("YouTube OAuth refresh returned no access_token")
    return token

def read_json_from_repo(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(path)}"
    d = gh_api(url)
    return json.loads(base64.b64decode(d["content"]).decode()), d["sha"]

def write_json_to_repo(path, payload, old_sha, message):
    body = {
        "message": message,
        "content": base64.b64encode((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()).decode(),
        "sha": old_sha,
    }
    url = f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(path)}"
    return gh_api(url, "PUT", body)["content"]["sha"]

def validate_phase11_run():
    d = gh_api(f"https://api.github.com/repos/{REPO}/actions/runs/{PHASE11_RUN_ID}")
    if d.get("name") != "Phase 11 Platform Publishing":
        die("Supplied run is not Phase 11 Platform Publishing")
    if d.get("status") != "completed" or d.get("conclusion") != "success":
        die("Supplied Phase 11 run is not completed successfully")
    if d.get("repository", {}).get("full_name") != REPO:
        die("Phase 11 run belongs to a different repository")
    return {
        "runId": int(PHASE11_RUN_ID),
        "headSha": d.get("head_sha"),
        "completedAt": d.get("updated_at"),
    }

def youtube_snapshot(video_id, token):
    params = urllib.parse.urlencode({
        "part": "snippet,contentDetails,statistics,status",
        "id": video_id,
    })
    d = google_json("https://www.googleapis.com/youtube/v3/videos?" + params, token)
    items = d.get("items", [])
    if not items:
        die(f"YouTube video {video_id} was not returned by videos.list")
    v = items[0]
    return {
        "videoId": v["id"],
        "title": v.get("snippet", {}).get("title"),
        "descriptionPresent": bool(v.get("snippet", {}).get("description")),
        "publishedAt": v.get("snippet", {}).get("publishedAt"),
        "channelId": v.get("snippet", {}).get("channelId"),
        "privacyStatus": v.get("status", {}).get("privacyStatus"),
        "uploadStatus": v.get("status", {}).get("uploadStatus"),
        "duration": v.get("contentDetails", {}).get("duration"),
        "definition": v.get("contentDetails", {}).get("definition"),
        "dimension": v.get("contentDetails", {}).get("dimension"),
        "viewCount": v.get("statistics", {}).get("viewCount"),
        "likeCount": v.get("statistics", {}).get("likeCount"),
        "commentCount": v.get("statistics", {}).get("commentCount"),
        "trackedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

def main():
    run = validate_phase11_run()
    ledger, _ = read_json_from_repo(str(LEDGER_PATH))
    pubs = ledger.get("publications", [])
    youtube_pubs = [x for x in pubs if x.get("platform") == "youtube" and x.get("status") == "published"]
    if not youtube_pubs:
        die("No published YouTube records found in Phase 11 ledger")

    token = refresh_youtube_token()
    snapshots = []
    for p in youtube_pubs:
        remote = p.get("remote", {})
        video_id = remote.get("videoId")
        if not video_id:
            die(f"Published YouTube record has no videoId: {p.get('clipFile')}")
        snap = youtube_snapshot(video_id, token)
        snap["clipFile"] = p["clipFile"]
        snap["videoSha256"] = p["videoSha256"]
        snapshots.append(snap)

    instagram_pubs = [x for x in pubs if x.get("platform") == "instagram" and x.get("status") == "published"]
    instagram_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if instagram_pubs and not instagram_token:
        die("Instagram publications exist but INSTAGRAM_ACCESS_TOKEN is missing")
    instagram_snapshots = []
    for p in instagram_pubs:
        media_id = p.get("remote", {}).get("mediaId")
        if not media_id:
            die(f"Published Instagram record has no mediaId: {p.get('clipFile')}")
        snap = instagram_snapshot(media_id, instagram_token)
        snap["clipFile"] = p["clipFile"]
        snap["videoSha256"] = p["videoSha256"]
        instagram_snapshots.append(snap)

    previous = None
    try:
        previous, tracking_sha = read_json_from_repo(str(TRACKING_PATH))
    except Exception as e:
        if "GitHub HTTP 404" in str(e):
            tracking_sha = None
        else:
            raise

    history = (previous or {}).get("snapshots", [])
    record = {
        "schemaVersion": 1,
        "phase": 12,
        "status": "verified",
        "phase11RunId": int(PHASE11_RUN_ID),
        "phase11HeadSha": run["headSha"],
        "trackedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platforms": ["youtube", "instagram"] if instagram_snapshots else ["youtube"],
        "videoCount": len(snapshots),
        "instagramVideoCount": len(instagram_snapshots),
        "snapshots": snapshots,
        "instagramSnapshots": instagram_snapshots,
    }
    history.append(record)
    payload = {"schemaVersion": 1, "snapshots": history}

    message = f"Record Phase 12 YouTube tracking snapshot for Phase 11 run {PHASE11_RUN_ID}"
    if tracking_sha:
        new_sha = write_json_to_repo(str(TRACKING_PATH), payload, tracking_sha, message)
    else:
        url = f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(str(TRACKING_PATH))}"
        body = {
            "message": message,
            "content": base64.b64encode((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()).decode(),
        }
        new_sha = gh_api(url, "PUT", body)["content"]["sha"]

    print(json.dumps({
        "phase12": "complete",
        "phase11RunId": PHASE11_RUN_ID,
        "trackedYouTubeVideos": len(snapshots),
        "videoIds": [x["videoId"] for x in snapshots],
        "trackingFile": str(TRACKING_PATH),
        "trackingCommitBlobSha": new_sha,
    }, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
