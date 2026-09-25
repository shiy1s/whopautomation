#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright

REPO=os.environ.get("GITHUB_REPOSITORY","shiy1s/whopautomation")
BASE="https://contentrewards.com"
QUEUE="state/phase14-content-rewards-submissions.json"
MAX_AGE=int(os.environ.get("MAX_AGE_MINUTES","30"))
ARTIFACTS=Path(os.environ.get("ARTIFACT_DIR","phase14-playwright-artifacts"))

def now(): return datetime.now(timezone.utc)
def die(m): raise RuntimeError(m)
def parse_utc(v): return datetime.fromisoformat(v.replace("Z","+00:00"))

def gh(path,method="GET",body=None):
    h={"Authorization":f"Bearer {os.environ['GH_TOKEN']}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"whopautomation-phase14-playwright"}
    if body is not None: h["Content-Type"]="application/json"
    r=Request(f"https://api.github.com/repos/{REPO}/{path}",method=method,headers=h,data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(r,timeout=30) as x: return json.loads(x.read().decode())
    except HTTPError as e: raise RuntimeError(f"GitHub HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}")

def read_queue():
    d=gh("contents/"+QUEUE); return json.loads(base64.b64decode(d["content"]).decode()),d["sha"]

def write_queue(state,sha,message):
    body={"message":message,"content":base64.b64encode((json.dumps(state,indent=2,sort_keys=True)+"\n").encode()).decode(),"sha":sha}
    return gh("contents/"+QUEUE,"PUT",body)

def storage_state():
    raw=os.environ.get("CONTENT_REWARDS_STORAGE_STATE_B64","").strip()
    if not raw: die("CONTENT_REWARDS_STORAGE_STATE_B64 is not configured")
    try: s=json.loads(base64.b64decode(raw).decode())
    except Exception as e: die(f"Invalid CONTENT_REWARDS_STORAGE_STATE_B64: {e}")
    if not isinstance(s,dict) or "cookies" not in s: die("Storage state is not a Playwright storageState object")
    return s

def visible(loc): return [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]
def body_text(page): return page.locator("body").inner_text(timeout=10000)

def artifact(page,name):
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(ARTIFACTS/f"{name}.png"),full_page=True)
    (ARTIFACTS/f"{name}.html").write_text(page.content(),encoding="utf-8")

def submit_control(page):
    out=[]; pat=re.compile(r"^(submit(?:\s+(?:clip|content|post))?|submit)$",re.I)
    for role in ("button","link"):
        loc=page.get_by_role(role,name=pat); out += visible(loc)
    if len(out)!=1: raise RuntimeError(f"ambiguous_submit_control:{len(out)}")
    return out[0]

def url_field(page):
    selectors=['input[type="url"]','input[name*="url" i]','input[placeholder*="link" i]','input[placeholder*="url" i]','textarea[placeholder*="link" i]','textarea[placeholder*="url" i]']
    out=[]; seen=set()
    for s in selectors:
        for e in visible(page.locator(s)):
            key=e.evaluate("e=>e.outerHTML")
            if key not in seen: seen.add(key); out.append(e)
    if len(out)!=1: raise RuntimeError(f"ambiguous_post_url_field:{len(out)}")
    return out[0]

def requirements_checkbox(page):
    boxes=visible(page.locator('input[type="checkbox"]'))
    if len(boxes)==1: return boxes[0]
    match=[]
    for b in boxes:
        t=b.evaluate("e=>{const l=e.closest('label');return ((l&&l.innerText)||'')+' '+((e.parentElement&&e.parentElement.innerText)||'')}")
        if re.search(r"accept|agree|requirement|terms|confirm",t,re.I): match.append(b)
    if len(match)==1: return match[0]
    raise RuntimeError(f"ambiguous_requirements_checkbox:{len(boxes)}")

def auth_check(page):
    if re.search(r"/login|/onboarding",page.url,re.I): die(f"Authentication required: {page.url}")
    if re.search(r"continue with google|create your creator account",body_text(page),re.I): die("Authenticated creator session not detected")

def campaign_url(cid):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}",cid): die(f"Invalid campaign UUID: {cid}")
    return f"{BASE}/discover/{cid}"

def process(page,item,dry):
    cid=item["campaignId"]; platform=item["platform"]; clip=item["clipFile"]; post=item["postUrl"]
    if platform not in {"youtube","instagram"}: die(f"Unsupported platform: {platform}")
    age=(now()-parse_utc(item["publishedAtUtc"])).total_seconds()/60
    if age < -2 or age > MAX_AGE: raise RuntimeError(f"outside_submission_window:{age:.2f}")
    page.goto(campaign_url(cid),wait_until="domcontentloaded",timeout=45000); page.wait_for_timeout(1200)
    auth_check(page)
    if cid.lower() not in page.url.lower(): die(f"Campaign navigation mismatch: {page.url}")
    text=body_text(page)
    if not re.search(r"youtube|instagram",text,re.I): die("Campaign page does not expose a supported platform")
    if re.search(r"apply to join|apply now|application required",text,re.I) and not re.search(r"joined|submit",text,re.I): die("Campaign requires application/join approval")
    submit=submit_control(page)
    if dry:
        artifact(page,f"preflight-{cid}-{platform}-{clip}")
        return {"status":"validated","campaignId":cid,"platform":platform,"clipFile":clip,"postUrl":post}
    submit.click(timeout=10000); page.wait_for_timeout(400)
    field=url_field(page); field.fill(post)
    box=requirements_checkbox(page)
    if not box.is_checked(): box.check()
    final=submit_control(page)
    if not final.is_enabled(): die("Submit control remains disabled after validation")
    before=page.url; final.click(timeout=10000); page.wait_for_timeout(1500)
    after=body_text(page)
    if re.search(r"already submitted|invalid|not eligible|rejected|submission failed|error",after,re.I): raise RuntimeError("submission_error_or_rejection_signal")
    if not re.search(r"submitted|under review|pending|submission received|success",after,re.I) and page.url==before: raise RuntimeError("submission_confirmation_not_verified")
    artifact(page,f"submitted-{cid}-{platform}-{clip}")
    return {"status":"submitted","campaignId":cid,"platform":platform,"clipFile":clip,"postUrl":post,"resultUrl":page.url}

def classify(e):
    m=str(e)
    if m.startswith("outside_submission_window"): return "expired"
    if m.startswith("ambiguous_") or "Authentication" in m or "not detected" in m or "requires application" in m: return "blocked"
    return "needs_manual_verification"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--dry-run",action="store_true"); a=ap.parse_args()
    state,sha=read_queue(); pending=[x for x in state.get("submissions",[]) if x.get("status")=="queued"]
    if not pending: print(json.dumps({"status":"nothing_to_do","pending":0})); return
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); ctx=browser.new_context(storage_state=storage_state()); page=ctx.new_page()
        for item in pending:
            try:
                r=process(page,item,a.dry_run); results.append(r)
                if not a.dry_run:
                    item["status"]="submitted"; item["submittedAtUtc"]=now().strftime("%Y-%m-%dT%H:%M:%SZ"); item["contentRewardsSubmission"]["status"]="submitted"
            except Exception as e:
                status=classify(e); artifact(page,f"failure-{item.get('campaignId')}-{item.get('platform')}-{item.get('clipFile')}")
                item["status"]=status; item["updatedAtUtc"]=now().strftime("%Y-%m-%dT%H:%M:%SZ"); item["error"]=str(e); item["contentRewardsSubmission"]["status"]=status
                results.append({"status":status,"campaignId":item.get("campaignId"),"platform":item.get("platform"),"clipFile":item.get("clipFile"),"error":str(e)})
        ctx.close(); browser.close()
    if a.dry_run: print(json.dumps({"status":"dry_run_complete","results":results},indent=2)); return
    c=write_queue(state,sha,f"Record Content Rewards submission worker results ({len(results)} jobs)")
    print(json.dumps({"status":"complete","results":results,"ledgerCommit":c.get("commit",{}).get("sha")},indent=2))

if __name__=="__main__":
    try: main()
    except Exception as e: print(f"ERROR: {e}",file=sys.stderr); sys.exit(1)
