#!/usr/bin/env python3
import os,sys,json,time,hashlib,base64,urllib.request,urllib.parse,urllib.error
from pathlib import Path

ROOT=Path(os.environ.get("PACKAGE_DIR","phase10"))
REPO=os.environ["GITHUB_REPOSITORY"]; TOKEN=os.environ["GH_TOKEN"]
PLATFORMS=[x for x in os.environ.get("PLATFORMS","all").split(",") if x]
if PLATFORMS==["all"]: PLATFORMS=["youtube","tiktok","instagram"]

def die(s): raise RuntimeError(s)
def jfile(p): return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def api(url,method="GET",body=None,token=None):
 d=None if body is None else json.dumps(body).encode()
 h={"User-Agent":"whopautomation-phase11","Accept":"application/vnd.github+json"}
 if token: h["Authorization"]="Bearer "+token
 if body is not None: h["Content-Type"]="application/json"
 try:
  r=urllib.request.urlopen(urllib.request.Request(url,data=d,headers=h,method=method),timeout=90)
  b=r.read(); return json.loads(b) if b else {}
 except urllib.error.HTTPError as e:
  raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}")
def form(url,fields):
 d=urllib.parse.urlencode(fields).encode()
 r=urllib.request.Request(url,data=d,headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
 try:
  return json.loads(urllib.request.urlopen(r,timeout=60).read())
 except urllib.error.HTTPError as e: die(e.read().decode(errors="replace")[:1200])
def http_json(url,method="GET",body=None,token=None):
 d=None if body is None else json.dumps(body).encode()
 h={"Content-Type":"application/json"} if body is not None else {}
 if token: h["Authorization"]="Bearer "+token
 try:
  r=urllib.request.urlopen(urllib.request.Request(url,data=d,headers=h,method=method),timeout=120)
  b=r.read(); return json.loads(b) if b else {}
 except urllib.error.HTTPError as e: die(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}")
def manifest():
 m=jfile(ROOT/"publish-manifest.json")
 if not m.get("complete") or m.get("status")!="ready_for_platform_publishing": die("Phase 10 package is not ready")
 if not m.get("publishingPolicy",{}).get("phase10DoesNotPublish"): die("Phase 10 policy invalid")
 if not m.get("publishingPolicy",{}).get("duplicatePostingForbidden"): die("duplicate-posting policy missing")
 if len(m.get("clips",[]))!=2: die("Phase 10 must contain exactly 2 clips")
 for c in m["clips"]:
  p=ROOT/"videos"/c["file"]
  if not p.exists() or sha(p)!=c["sha256"]: die(f"video missing/checksum mismatch: {c['file']}")
  if not (ROOT/c["metadataFile"]).exists(): die(f"metadata missing: {c['metadataFile']}")
 return m
def ledger():
 u=f"https://api.github.com/repos/{REPO}/contents/state/phase11-publication-ledger.json"
 try:
  d=api(u,token=TOKEN); return json.loads(base64.b64decode(d["content"]).decode()),d["sha"]
 except RuntimeError as e:
  if "HTTP 404" in str(e): return {"schemaVersion":1,"publications":[]},None
  raise
def save_ledger(l,oldsha):
 body={"message":"Record Phase 11 publication","content":base64.b64encode((json.dumps(l,indent=2,sort_keys=True)+"\n").encode()).decode()}
 if oldsha: body["sha"]=oldsha
 u=f"https://api.github.com/repos/{REPO}/contents/state/phase11-publication-ledger.json"
 return api(u,"PUT",body,token=TOKEN)["content"]["sha"]
def duplicate(l,c,p):
 return any(x.get("clipFile")==c["file"] and x.get("platform")==p and x.get("videoSha256")==c["sha256"] and x.get("status")=="published" for x in l.get("publications",[]))
def youtube_access_token():
 fields={
  "client_id":os.environ["YOUTUBE_CLIENT_ID"],
  "client_secret":os.environ["YOUTUBE_CLIENT_SECRET"],
  "refresh_token":os.environ["YOUTUBE_REFRESH_TOKEN"],
  "grant_type":"refresh_token",
 }
 d=form("https://oauth2.googleapis.com/token",fields)
 token=d.get("access_token")
 if not token: die("YouTube OAuth refresh returned no access_token")
 scope=set((d.get("scope") or "").split())
 required="https://www.googleapis.com/auth/youtube.upload"
 if scope and required not in scope: die("YouTube OAuth token does not include youtube.upload scope")
 return token,scope

def youtube_preflight():
 token,scope=youtube_access_token()
 # youtube.upload is intentionally the least-privilege scope used by the real
 # videos.insert publisher. It does not authorize channels.list, so channel
 # discovery must not be used as a preflight check.
 q=urllib.parse.urlencode({"access_token":token})
 info=http_json("https://oauth2.googleapis.com/tokeninfo?"+q)
 if info.get("aud") and info.get("aud")!=os.environ["YOUTUBE_CLIENT_ID"]:
  die("YouTube access token audience does not match YOUTUBE_CLIENT_ID")
 token_scope=set((info.get("scope") or "").split())
 required="https://www.googleapis.com/auth/youtube.upload"
 if required not in token_scope:
  die("YouTube access token does not contain youtube.upload scope")
 expires=info.get("expires_in")
 try:
  if expires is not None and int(expires)<=0: die("YouTube access token is expired")
 except (TypeError,ValueError):
  die("YouTube tokeninfo returned an invalid expires_in value")
 print(json.dumps({
  "youtubeOAuth":"pass",
  "youtubeToken":"pass",
  "youtubeApi":"upload_scope_verified",
  "scopeChecked":"youtube.upload"
 }))

def youtube(c):
 from google.oauth2.credentials import Credentials
 from google.auth.transport.requests import Request
 from googleapiclient.discovery import build
 from googleapiclient.http import MediaFileUpload
 cr=Credentials(None,refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],token_uri="https://oauth2.googleapis.com/token",client_id=os.environ["YOUTUBE_CLIENT_ID"],client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],scopes=["https://www.googleapis.com/auth/youtube.upload"])
 cr.refresh(Request()); yt=build("youtube","v3",credentials=cr,cache_discovery=False)
 md=jfile(ROOT/c["metadataFile"])["youtubeShorts"]
 body={"snippet":{"title":md["title"],"description":md["description"],"tags":md["tags"],"categoryId":"20"},"status":{"privacyStatus":(os.environ.get("YOUTUBE_PRIVACY_STATUS") or "public"),"selfDeclaredMadeForKids":False}}
 req=yt.videos().insert(part="snippet,status",body=body,media_body=MediaFileUpload(str(ROOT/"videos"/c["file"]),mimetype="video/mp4",resumable=True,chunksize=8*1024*1024))
 r=req.execute(); return {"videoId":r["id"],"privacyStatus":r.get("status",{}).get("privacyStatus")}
def tiktok_token():
 if os.getenv("TIKTOK_ACCESS_TOKEN"): return os.environ["TIKTOK_ACCESS_TOKEN"]
 d=form("https://open.tiktokapis.com/v2/oauth/token/",{"client_key":os.environ["TIKTOK_CLIENT_KEY"],"client_secret":os.environ["TIKTOK_CLIENT_SECRET"],"grant_type":"refresh_token","refresh_token":os.environ["TIKTOK_REFRESH_TOKEN"]})
 if d.get("refresh_token") and d["refresh_token"]!=os.environ.get("TIKTOK_REFRESH_TOKEN"): print("::warning::TikTok returned a rotated refresh token; update TIKTOK_REFRESH_TOKEN.")
 return d["access_token"]
def tiktok(c):
 t=tiktok_token(); info=http_json("https://open.tiktokapis.com/v2/post/publish/creator_info/query/","POST",{},t)
 opts=info.get("data",{}).get("privacy_level_options",[])
 if "PUBLIC_TO_EVERYONE" not in opts: die("TikTok PUBLIC_TO_EVERYONE is not available; refusing to override creator settings")
 md=jfile(ROOT/c["metadataFile"])["tiktok"]; p=ROOT/"videos"/c["file"]; raw=p.read_bytes()
 body={"post_info":{"title":md["caption"],"privacy_level":"PUBLIC_TO_EVERYONE","disable_duet":True,"disable_comment":False,"disable_stitch":True},"source_info":{"source":"FILE_UPLOAD","video_size":len(raw),"chunk_size":len(raw),"total_chunk_count":1}}
 d=http_json("https://open.tiktokapis.com/v2/post/publish/video/init/","POST",body,t); url=d.get("data",{}).get("upload_url"); pid=d.get("data",{}).get("publish_id")
 if not url or not pid: die("TikTok init missing upload_url/publish_id")
 r=urllib.request.Request(url,data=raw,headers={"Content-Type":"video/mp4","Content-Length":str(len(raw)),"Content-Range":f"bytes 0-{len(raw)-1}/{len(raw)}"},method="PUT")
 urllib.request.urlopen(r,timeout=180).read()
 return {"publishId":pid,"privacyLevel":"PUBLIC_TO_EVERYONE"}
def instagram(c):
 tok=os.environ["INSTAGRAM_ACCESS_TOKEN"]; ig=os.environ["INSTAGRAM_USER_ID"]; v=(os.environ.get("INSTAGRAM_GRAPH_VERSION") or "v25.0"); tag=os.environ["PHASE11_RELEASE_TAG"]
 md=jfile(ROOT/c["metadataFile"])["instagram"]; video=f"https://github.com/{REPO}/releases/download/{tag}/{c['file']}"
 q=urllib.parse.urlencode({"media_type":"REELS","video_url":video,"caption":md["caption"],"share_to_feed":"true","access_token":tok})
 d=http_json(f"https://graph.instagram.com/{v}/{ig}/media?{q}","POST"); cid=d.get("id")
 if not cid: die("Instagram Login Reel container creation failed")
 end=time.time()+600
 while time.time()<end:
  q=urllib.parse.urlencode({"fields":"status_code,status","access_token":tok}); s=http_json(f"https://graph.instagram.com/{v}/{cid}?{q}")
  if s.get("status_code")=="FINISHED": break
  if s.get("status_code") in ("ERROR","EXPIRED"): die(f"Instagram Login container failed: {s}")
  time.sleep(15)
 else: die("Instagram Login container timed out")
 q=urllib.parse.urlencode({"creation_id":cid,"access_token":tok}); d=http_json(f"https://graph.instagram.com/{v}/{ig}/media_publish?{q}","POST")
 if not d.get("id"): die("Instagram Login publish returned no media id")
 return {"mediaId":d["id"],"containerId":cid}
def main():
 m=manifest()
 req={"youtube":["YOUTUBE_CLIENT_ID","YOUTUBE_CLIENT_SECRET","YOUTUBE_REFRESH_TOKEN"],"tiktok":["TIKTOK_CLIENT_KEY","TIKTOK_CLIENT_SECRET","TIKTOK_REFRESH_TOKEN"],"instagram":["INSTAGRAM_ACCESS_TOKEN","INSTAGRAM_USER_ID"]}
 for p in PLATFORMS:
  miss=[x for x in req[p] if not os.getenv(x)]
  if p=="tiktok" and os.getenv("TIKTOK_ACCESS_TOKEN"): miss=[]
  if miss: die(f"{p} credential preflight failed: missing {', '.join(miss)}")
 if os.getenv("PHASE11_PREFLIGHT")=="1":
  if "youtube" in PLATFORMS:
   youtube_preflight()
  print(json.dumps({"preflight":"pass","platforms":PLATFORMS,"clipCount":2,"phase9RunId":m["phase9RunId"]})); return
 if os.getenv("CONFIRM_PUBLISH")!="PUBLISH": die("Publishing locked: set confirm_publish=PUBLISH")
 l,ls=ledger()
 for p in PLATFORMS:
  for c in m["clips"]:
   if duplicate(l,c,p): print(f"SKIP duplicate: {p} {c['file']}"); continue
   print(f"PUBLISH {p} {c['file']}")
   r=youtube(c) if p=="youtube" else tiktok(c) if p=="tiktok" else instagram(c)
   print(json.dumps({"publicationResult":r,"clipFile":c["file"],"platform":p}))
   l.setdefault("publications",[]).append({"clipFile":c["file"],"platform":p,"videoSha256":c["sha256"],"publishedAtUtc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"status":"published","remote":r})
   ls=save_ledger(l,ls); print(json.dumps(r))
 print("PHASE11_COMPLETE")
if __name__=="__main__":
 try: main()
 except Exception as e: print("ERROR:",e,file=sys.stderr); sys.exit(1)
