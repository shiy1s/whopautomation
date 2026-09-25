#!/usr/bin/env python3
import base64, json, os, re, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

REPO=os.environ["GITHUB_REPOSITORY"]; RUN_ID=os.environ["PHASE11_RUN_ID"].strip()
CAMPAIGN_ID=os.environ["CAMPAIGN_ID"].strip(); CAMPAIGN_NAME=os.environ["CAMPAIGN_NAME"].strip()
CAMPAIGN_STATUS=os.environ.get("CAMPAIGN_STATUS","unknown").strip().lower()
CONFIRM=os.environ.get("CONFIRM_SUBMISSION","").strip(); MAX_AGE=int(os.environ.get("MAX_AGE_MINUTES","30"))
PLATFORMS={"youtube","instagram"}
LEDGER_PATH="state/phase11-publication-ledger.json"; SUBMISSION_PATH="state/phase14-content-rewards-submissions.json"
YOUTUBE_RE=re.compile(r"^https://(?:www\.)?youtube\.com/shorts/([A-Za-z0-9_-]+)(?:\?.*)?$")
INSTAGRAM_RE=re.compile(r"^https://(?:www\.)?instagram\.com/reel/([A-Za-z0-9_-]+)(?:/)?(?:\?.*)?$")

def die(msg): raise RuntimeError(msg)
def now(): return datetime.now(timezone.utc)
def parse_utc(v): return datetime.fromisoformat(v.replace("Z","+00:00"))

def gh_api(path,method="GET",body=None):
    req=urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}",method=method,headers={"Authorization":f"Bearer {os.environ['GH_TOKEN']}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"whopautomation-phase14"},data=None if body is None else json.dumps(body).encode())
    if body is not None: req.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())
    except urllib.error.HTTPError as e: raise RuntimeError(f"GitHub HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}")

def read_json(path):
    d=gh_api("contents/"+urllib.parse.quote(path)); return json.loads(base64.b64decode(d["content"]).decode()),d["sha"]

def write_json(path,payload,sha,message):
    b={"message":message,"content":base64.b64encode((json.dumps(payload,indent=2,sort_keys=True)+"\n").encode()).decode(),"sha":sha}
    return gh_api("contents/"+urllib.parse.quote(path),"PUT",b)["content"]["sha"]

def validate_inputs():
    if not re.fullmatch(r"[0-9]+",RUN_ID): die("PHASE11_RUN_ID must be numeric")
    if not CAMPAIGN_ID or not CAMPAIGN_NAME: die("CAMPAIGN_ID and CAMPAIGN_NAME are required")
    if CAMPAIGN_STATUS!="active": die("Submission gate is locked: CAMPAIGN_STATUS must be active")
    if not 1<=MAX_AGE<=30: die("MAX_AGE_MINUTES must be between 1 and 30")
    if CONFIRM!="SUBMIT_READY": die("Submission gate is locked: set CONFIRM_SUBMISSION=SUBMIT_READY")

def publication_url(platform,remote):
    if platform=="youtube":
        vid=remote.get("videoId")
        if not vid: die("Published YouTube record has no videoId")
        return f"https://www.youtube.com/shorts/{vid}"
    url=remote.get("permalink")
    if not url: die("Published Instagram record has no verified permalink")
    if not INSTAGRAM_RE.fullmatch(url): die("Instagram permalink is not a valid reel URL")
    return url

def validate_url(platform,url,remote):
    if platform=="youtube":
        m=YOUTUBE_RE.fullmatch(url)
        if not m or m.group(1)!=remote.get("videoId"): die("YouTube URL does not match exact published videoId")
    elif not INSTAGRAM_RE.fullmatch(url): die("Instagram URL must be an instagram.com/reel/<id> URL")

def main():
    validate_inputs()
    run=gh_api(f"actions/runs/{RUN_ID}")
    if run.get("name")!="Phase 11 Platform Publishing" or run.get("status")!="completed" or run.get("conclusion")!="success":
        die("Supplied Phase 11 run is not a successful Phase 11 Platform Publishing run")
    ledger,_=read_json(LEDGER_PATH)
    pubs=[p for p in ledger.get("publications",[]) if p.get("phase11RunId")==int(RUN_ID) and p.get("status")=="published" and p.get("platform") in PLATFORMS and p.get("videoSha256")]
    if not pubs: die(f"No eligible YouTube/Instagram publications exist for Phase 11 run {RUN_ID}")
    try: state,state_sha=read_json(SUBMISSION_PATH)
    except RuntimeError as e:
        if "HTTP 404" not in str(e): raise
        state,state_sha={"schemaVersion":2,"submissions":[]},None
    existing=state.get("submissions",[]); prepared=[]; skipped=[]
    for p in pubs:
        platform=p["platform"]; clip=p.get("clipFile")
        if not clip: skipped.append({"reason":"missing_clip_file"}); continue
        age=(now()-parse_utc(p["publishedAtUtc"])).total_seconds()/60
        if age < -2 or age > MAX_AGE:
            skipped.append({"platform":platform,"clipFile":clip,"reason":"outside_submission_window","ageMinutes":round(age,2)}); continue
        url=publication_url(platform,p.get("remote",{})); validate_url(platform,url,p.get("remote",{}))
        dup=any(x.get("campaignId")==CAMPAIGN_ID and x.get("platform")==platform and x.get("clipFile")==clip and x.get("postUrl")==url and x.get("status") in {"prepared","queued","submitted","approved","pending","rejected"} for x in existing)
        if dup: skipped.append({"platform":platform,"clipFile":clip,"postUrl":url,"reason":"duplicate"}); continue
        rec={"schemaVersion":2,"status":"queued","campaignId":CAMPAIGN_ID,"campaignName":CAMPAIGN_NAME,"platform":platform,"clipFile":clip,"postUrl":url,"publishedAtUtc":p["publishedAtUtc"],"preparedAtUtc":now().strftime("%Y-%m-%dT%H:%M:%SZ"),"ageMinutesAtPreparation":round(age,2),"phase11RunId":int(RUN_ID),"videoSha256":p["videoSha256"],"remote":p.get("remote",{}),"contentRewardsSubmission":{"automation":"playwright-worker","status":"queued","reason":"Deterministic validation passed; authorized browser worker may submit this exact public URL."}}
        existing.append(rec); prepared.append(rec)
    state["schemaVersion"]=2; state["submissions"]=existing
    if prepared or state_sha is None:
        if state_sha: ledger_sha=write_json(SUBMISSION_PATH,state,state_sha,f"Queue Content Rewards submissions for Phase 11 run {RUN_ID}")
        else:
            body={"message":f"Initialize Content Rewards submission ledger for Phase 11 run {RUN_ID}","content":base64.b64encode((json.dumps(state,indent=2,sort_keys=True)+"\n").encode()).decode()}
            ledger_sha=gh_api("contents/"+urllib.parse.quote(SUBMISSION_PATH),"PUT",body)["content"]["sha"]
    else: ledger_sha=None
    print(json.dumps({"phase14":"submission_queue_prepared","status":"queued" if prepared else "nothing_new","campaignId":CAMPAIGN_ID,"campaignName":CAMPAIGN_NAME,"phase11RunId":int(RUN_ID),"preparedCount":len(prepared),"skippedCount":len(skipped),"prepared":prepared,"skipped":skipped,"ledger":SUBMISSION_PATH,"ledgerBlobSha":ledger_sha},indent=2))

if __name__=="__main__":
    try: main()
    except Exception as e: print("ERROR:",e); raise
