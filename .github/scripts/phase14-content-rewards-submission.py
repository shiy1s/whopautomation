#!/usr/bin/env python3
import base64, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

REPO = os.environ["GITHUB_REPOSITORY"]
RUN_ID = os.environ["PHASE11_RUN_ID"]
CAMPAIGN_ID = os.environ["CAMPAIGN_ID"].strip()
CAMPAIGN_NAME = os.environ["CAMPAIGN_NAME"].strip()
CAMPAIGN_STATUS = os.environ.get("CAMPAIGN_STATUS", "unknown").strip().lower()
PLATFORM = os.environ["PLATFORM"].strip().lower()
CLIP_FILE = os.environ["CLIP_FILE"].strip()
POST_URL = os.environ.get("POST_URL", "").strip()
CONFIRM = os.environ.get("CONFIRM_SUBMISSION", "").strip()
MAX_AGE = int(os.environ.get("MAX_AGE_MINUTES", "30"))

LEDGER_PATH = "state/phase11-publication-ledger.json"
SUBMISSION_PATH = "state/phase14-content-rewards-submissions.json"

YOUTUBE_RE = re.compile(r"^https://(?:www\.)?youtube\.com/shorts/([A-Za-z0-9_-]+)(?:\?.*)?$")
INSTAGRAM_RE = re.compile(r"^https://(?:www\.)?instagram\.com/reel/([A-Za-z0-9_-]+)(?:/)?(?:\?.*)?$")

def die(msg):
    raise RuntimeError(msg)

def now():
    return datetime.now(timezone.utc)

def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def gh_api(path, method="GET", body=None):
    token = os.environ["GH_TOKEN"]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "whopautomation-phase14",
        },
        data=None if body is None else json.dumps(body).encode(),
    )
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub HTTP {error.code}: {error.read().decode(errors='replace')[:1200]}"
        )

def read_json(path):
    data = gh_api("contents/" + urllib.parse.quote(path))
    return json.loads(base64.b64decode(data["content"]).decode()), data["sha"]

def write_json(path, payload, sha, message):
    body = {
        "message": message,
        "content": base64.b64encode(
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        ).decode(),
        "sha": sha,
    }
    return gh_api("contents/" + urllib.parse.quote(path), "PUT", body)["content"]["sha"]

def validate_inputs():
    if not re.fullmatch(r"[0-9]+", RUN_ID):
        die("PHASE11_RUN_ID must be numeric")
    if not CAMPAIGN_ID:
        die("CAMPAIGN_ID is required")
    if not CAMPAIGN_NAME:
        die("CAMPAIGN_NAME is required")
    if CAMPAIGN_STATUS != "active":
        die("Submission gate is locked: CAMPAIGN_STATUS must be active")
    if PLATFORM not in {"youtube", "instagram"}:
        die("Only youtube and instagram are enabled in Phase 14; TikTok remains deferred")
    if CLIP_FILE not in {"clip_01.mp4", "clip_02.mp4"}:
        die("Unsupported clip_file")
    if MAX_AGE < 1 or MAX_AGE > 30:
        die("MAX_AGE_MINUTES must be between 1 and 30")
    if CONFIRM != "SUBMIT_READY":
        die("Submission gate is locked: set CONFIRM_SUBMISSION=SUBMIT_READY")

def expected_url(remote):
    if PLATFORM == "youtube":
        video_id = remote.get("videoId")
        if not video_id:
            die("Published YouTube record has no videoId")
        return f"https://www.youtube.com/shorts/{video_id}"
    if not remote.get("mediaId"):
        die("Published Instagram record has no mediaId")
    return None

def validate_url(url, remote):
    if PLATFORM == "youtube":
        match = YOUTUBE_RE.fullmatch(url)
        if not match or match.group(1) != remote.get("videoId"):
            die("YouTube URL does not match the exact published videoId")
    else:
        if not INSTAGRAM_RE.fullmatch(url):
            die("Instagram URL must be an instagram.com/reel/<id> URL")

def main():
    validate_inputs()

    run = gh_api(f"actions/runs/{RUN_ID}")
    if run.get("name") != "Phase 11 Platform Publishing":
        die("Supplied run is not Phase 11 Platform Publishing")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        die("Supplied Phase 11 run is not completed successfully")

    ledger, _ = read_json(LEDGER_PATH)
    matches = [
        item for item in ledger.get("publications", [])
        if item.get("platform") == PLATFORM
        and item.get("clipFile") == CLIP_FILE
        and item.get("status") == "published"
    ]
    if not matches:
        die(f"No published {PLATFORM} record exists for {CLIP_FILE}")

    publication = max(matches, key=lambda item: item.get("publishedAtUtc", ""))
    if publication.get("phase11RunId") != int(RUN_ID):
        die("Supplied Phase 11 run ID does not match the exact publication record")
    if not publication.get("videoSha256"):
        die("Publication record is missing videoSha256")

    published_at = parse_utc(publication["publishedAtUtc"])
    age = (now() - published_at).total_seconds() / 60
    if age < -2:
        die("Publication timestamp is in the future; refusing submission")
    if age > MAX_AGE:
        die(
            f"Submission window expired: publication is {age:.1f} minutes old; "
            f"maximum is {MAX_AGE} minutes"
        )

    url = POST_URL or expected_url(publication.get("remote", {}))
    if PLATFORM == "instagram" and not POST_URL:
        die("Instagram permalink is required because it cannot be derived from mediaId")
    validate_url(url, publication.get("remote", {}))

    state = {"schemaVersion": 1, "submissions": []}
    state_sha = None
    try:
        state, state_sha = read_json(SUBMISSION_PATH)
    except RuntimeError as error:
        if "HTTP 404" not in str(error):
            raise

    duplicate = any(
        item.get("campaignId") == CAMPAIGN_ID
        and item.get("platform") == PLATFORM
        and item.get("clipFile") == CLIP_FILE
        and item.get("postUrl") == url
        and item.get("status") in {"prepared", "submitted", "approved", "pending", "rejected"}
        for item in state.get("submissions", [])
    )
    if duplicate:
        die("Duplicate submission candidate already exists for this campaign, clip, platform and URL")

    record = {
        "schemaVersion": 1,
        "status": "prepared",
        "campaignId": CAMPAIGN_ID,
        "campaignName": CAMPAIGN_NAME,
        "platform": PLATFORM,
        "clipFile": CLIP_FILE,
        "postUrl": url,
        "publishedAtUtc": publication["publishedAtUtc"],
        "preparedAtUtc": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ageMinutesAtPreparation": round(age, 2),
        "phase11RunId": int(RUN_ID),
        "videoSha256": publication["videoSha256"],
        "remote": publication.get("remote", {}),
        "contentRewardsSubmission": {
            "automation": "manual-gated",
            "reason": "No supported public Content Rewards submission API was verified; browser automation is intentionally not used."
        }
    }
    state.setdefault("submissions", []).append(record)

    if state_sha:
        new_sha = write_json(
            SUBMISSION_PATH, state, state_sha,
            f"Prepare Content Rewards submission for {CLIP_FILE}"
        )
    else:
        body = {
            "message": f"Initialize Content Rewards submission ledger for {CLIP_FILE}",
            "content": base64.b64encode(
                (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
            ).decode()
        }
        new_sha = gh_api(
            "contents/" + urllib.parse.quote(SUBMISSION_PATH), "PUT", body
        )["content"]["sha"]

    print(json.dumps({
        "phase14": "submission_candidate_ready",
        "status": "prepared",
        "campaignId": CAMPAIGN_ID,
        "campaignName": CAMPAIGN_NAME,
        "platform": PLATFORM,
        "clipFile": CLIP_FILE,
        "postUrl": url,
        "ageMinutes": round(age, 2),
        "ledger": SUBMISSION_PATH,
        "ledgerBlobSha": new_sha
    }, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR:", error, file=sys.stderr)
        sys.exit(1)
