const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const { chromium } = require('playwright');

const sleep = ms => new Promise(r => setTimeout(r, ms));

const isMediaUrl = u => {
  if (!u || typeof u !== 'string') return false;
  return /\.(mp4|m3u8|ts|mov|webm)(?:[?#]|$)/i.test(u) ||
         /video\/|application\/(vnd\.apple\.mpegurl|x-mpegurl)/i.test(u);
};

const isMediaType = ct => {
  if (!ct || typeof ct !== 'string') return false;
  return /^(video\/|audio\/)/i.test(ct) || /mpegurl|quicktime|webm|mp2t/i.test(ct);
};

const collectStrings = (obj, out = new Set()) => {
  if (!obj) return out;
  if (typeof obj === 'string') {
    if (/^https?:\/\//i.test(obj) && isMediaUrl(obj)) out.add(obj);
  } else if (Array.isArray(obj)) {
    for (const item of obj) collectStrings(item, out);
  } else if (typeof obj === 'object') {
    for (const k of Object.keys(obj)) collectStrings(obj[k], out);
  }
  return out;
};

const runFFprobe = filePath => {
  try {
    const cmd = `ffprobe -v error -show_entries format=duration,size,bit_rate -select_streams v:0 -show_entries stream=width,height,codec_name -of json "${filePath}"`;
    const out = cp.execSync(cmd, { encoding: 'utf8' });
    const data = JSON.parse(out);
    const duration = parseFloat(data.format?.duration || 0);
    const width = data.streams?.[0]?.width || 0;
    const height = data.streams?.[0]?.height || 0;
    const size = parseInt(data.format?.size || 0, 10);
    return { valid: duration >= 10 && width > 0 && height > 0, duration, width, height, size, raw: data };
  } catch (e) {
    const stderr = e.stderr ? String(e.stderr).trim().slice(0, 500) : '';
    let fileHead = '';
    try { fileHead = fs.readFileSync(filePath).slice(0, 24).toString('hex'); } catch {}
    return { valid: false, error: (stderr || e.message), fileHead };
  }
};

const extractFrames = (mediaPath, outDir, duration, frameCount = 12) => {
  fs.mkdirSync(outDir, { recursive: true });
  const frames = [];
  const step = duration / (frameCount + 1);
  for (let i = 1; i <= frameCount; i++) {
    const ts = (i * step).toFixed(2);
    const frameName = `frame_${String(i).padStart(2, '0')}.jpg`;
    const framePath = path.join(outDir, frameName);
    const cmd = `ffmpeg -y -ss ${ts} -i "${mediaPath}" -vframes 1 -q:v 2 "${framePath}"`;
    cp.execSync(cmd, { stdio: 'ignore' });
    if (!fs.existsSync(framePath) || fs.statSync(framePath).size < 1000) {
      throw new Error(`Failed to extract valid frame ${i} at timestamp ${ts}s`);
    }
    const stat = fs.statSync(framePath);
    frames.push({
      frameIndex: i,
      fileName: frameName,
      filePath: path.relative(path.resolve('phase4-input'), framePath).replace(/\\/g, '/'),
      timestampSeconds: parseFloat(ts),
      fileSizeBytes: stat.size
    });
  }
  return frames;
};

(async () => {
  const sourceType = String(process.env.INPUT_SOURCE_TYPE || '').trim().toLowerCase();
  const sourceUrl = String(process.env.INPUT_SOURCE_URL || '').trim();
  const campaignId = String(process.env.INPUT_CAMPAIGN_ID || '').trim();
  if (!['mediasilo','googledrive'].includes(sourceType)) throw new Error('Unsupported Phase 4 source_type: '+sourceType);
  if (!sourceUrl) throw new Error('Missing Phase 4 source_url.');
  console.log('=== Starting Phase 4 source adapter: '+sourceType+' ===');
  console.log('Campaign: '+campaignId+' | Source: '+sourceUrl);

  const outDir = path.resolve('phase4-input');
  const tmpMediaDir = path.resolve('.tmp-media');
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.rmSync(tmpMediaDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });
  fs.mkdirSync(tmpMediaDir, { recursive: true });

  const networkLog = [];
  const phase4Diagnostics = {
    startedAt: new Date().toISOString(),
    sourceType,
    sourceUrl,
    campaignId,
    assets: [],
    failures: []
  };

  const extractVerified = (items) => {
    const verified = [];
    for (const item of items) {
      const filePath = item.filePath;
      const probe = runFFprobe(filePath);
      if (!probe.valid) {
        phase4Diagnostics.failures.push(item.fileName+': ffprobe failed: '+(probe.error||'unknown'));
        continue;
      }
      if (probe.duration < 10) {
        phase4Diagnostics.failures.push(item.fileName+': duration below 10 seconds');
        continue;
      }
      verified.push({...item, probe});
    }
    return verified;
  };

  let verifiedSources = [];

  if (sourceType === 'googledrive') {
    console.log('Resolving Google Drive source with gdown...');
    let listing;
    try {
      const raw = cp.execFileSync('gdown', [sourceUrl, '--json', '--quiet'], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
      listing = JSON.parse(raw);
    } catch (e) {
      throw new Error('GOOGLE_DRIVE_LIST_FAILED: '+(e.stderr ? String(e.stderr) : e.message));
    }
    const entries = Array.isArray(listing) ? listing : [];
    const videoEntries = entries
      .filter(x => x && typeof x.url === 'string' && /\.(mp4|mov|m4v|webm|mkv)$/i.test(String(x.path||'')))
      .sort((a,b)=>String(a.path).localeCompare(String(b.path)));
    if (videoEntries.length < 2) throw new Error('GOOGLE_DRIVE_SOURCE_INSUFFICIENT_VIDEO: found '+videoEntries.length+' downloadable video files; at least 2 are required by the current render contract.');
    const selected = videoEntries.slice(0,2);
    console.log('Google Drive exposed '+videoEntries.length+' video files; deterministically selected 2.');
    for (const entry of selected) {
      const safeName = path.basename(String(entry.path||'video.mp4')).replace(/[^a-zA-Z0-9._-]+/g,'_');
      const dest = path.join(tmpMediaDir, safeName);
      try {
        cp.execFileSync('gdown', ['--continue','--retries','3',String(entry.url),'-O',dest], {stdio:'inherit'});
        if (!fs.existsSync(dest) || fs.statSync(dest).size < 100000) throw new Error('Downloaded file is missing or too small.');
        const hash = require('crypto').createHash('sha256').update(sourceUrl+'|'+entry.path).digest('hex').slice(0,24);
        verifiedSources.push({assetId:'gdrive-'+hash,fileName:safeName,folder:String(entry.path).split('/').slice(0,-1).join('/')||'root',filePath:dest,sourceUrl:String(entry.url)});
      } catch (e) {
        phase4Diagnostics.failures.push(String(entry.path)+': '+e.message);
      }
    }
  } else {
    // MediaSilo keeps the proven Phase 2 inventory path intact. The inventory is
    // consumed only when this adapter is explicitly selected.
    const inventoryPath = path.resolve('mediasilo-inventory.json');
    if (!fs.existsSync(inventoryPath)) throw new Error('Missing committed Phase 2 MediaSilo inventory.');
    let inv;
    try { inv = JSON.parse(fs.readFileSync(inventoryPath,'utf8')); }
    catch (e) { throw new Error('Invalid mediasilo-inventory.json: '+e.message); }
    const assets = (inv.assets || []).filter(a => a.type === 'video');
    if (assets.length !== 2) throw new Error('MediaSilo inventory contract requires exactly 2 video assets; found '+assets.length);
    const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});
    const context = await browser.newContext({viewport:{width:1920,height:1080},userAgent:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'});
    const mediasiloApiHeaders = {};
    context.on('request', req => { const h=req.headers(); if(h['x-key']) mediasiloApiHeaders['x-key']=h['x-key']; if(h['x-secret']) mediasiloApiHeaders['x-secret']=h['x-secret']; });
    const reviewUrl = inv.source?.reviewUrl || sourceUrl;
    const initPage = await context.newPage();
    await initPage.goto(reviewUrl,{waitUntil:'networkidle',timeout:45000}).catch(()=>{});
    await sleep(3000); await initPage.close();

    for (const a of assets) {
      const candidates = new Map();
      const page = await context.newPage();
      page.on('response', async response => {
        try {
          const ct=String(response.headers()['content-type']||'').toLowerCase(), url=response.url();
          networkLog.push({url,status:response.status(),contentType:ct,assetId:a.assetId});
          if(isMediaType(ct)||isMediaUrl(url)) candidates.set(url,{url,contentType:ct,source:'network-response'});
          if(ct.includes('json')) { try { const body=await response.json(); const out=new Set(); collectStrings(body,out); for(const u of out) if(isMediaUrl(u)) candidates.set(u,{url:u,contentType:ct,source:'json-body'}); } catch{} }
        } catch{}
      });
      const targetUrl=a.assetUrl||reviewUrl+'/'+a.assetId;
      await page.goto(targetUrl,{waitUntil:'networkidle',timeout:45000}).catch(()=>{});
      await sleep(4000);
      if(candidates.size===0&&mediasiloApiHeaders['x-key']) {
        try {
          const apiRes=await context.request.get('https://api.mediasilo.com/v3/quicklinks/6a91d42a1c9dd13bd6636b14/assets/'+a.assetId,{headers:{...mediasiloApiHeaders,Referer:reviewUrl,Accept:'application/json'}});
          if(apiRes.ok()){const body=await apiRes.json();const found=new Set();collectStrings(body,found);for(const u of found) if(isMediaUrl(u)) candidates.set(u,{url:u,contentType:'application/json',source:'api-fallback'});}
        } catch{}
      }
      await page.close();
      let verifiedMediaFile=null, probeResult=null;
      const candidateEvidence=[];
      for(const [candidateUrl,info] of candidates.entries()){
        const tempPath=path.join(tmpMediaDir,a.assetId+'_candidate_'+Date.now()+'.mp4');
        try{
          const fetchRes=await context.request.get(candidateUrl,{timeout:60000,headers:{Referer:reviewUrl,Accept:'*/*'}});
          const status=fetchRes.status(), resCt=String(fetchRes.headers()['content-type']||'').toLowerCase();
          if(!fetchRes.ok()){candidateEvidence.push({url:candidateUrl,source:info.source,status,contentType:resCt,outcome:'http-not-ok'});continue;}
          const buf=await fetchRes.body();
          if(buf.length<100000){candidateEvidence.push({url:candidateUrl,source:info.source,status,contentType:resCt,bytes:buf.length,outcome:'too-small'});continue;}
          fs.writeFileSync(tempPath,buf);
          const probe=runFFprobe(tempPath);
          if(probe.valid){verifiedMediaFile=tempPath;probeResult=probe;candidateEvidence.push({url:candidateUrl,source:info.source,status,contentType:resCt,bytes:buf.length,outcome:'verified'});break;}
          fs.unlinkSync(tempPath);
        }catch(e){candidateEvidence.push({url:candidateUrl,source:info.source,outcome:'fetch-error',error:e.message});}
      }
      if(verifiedMediaFile&&probeResult) verifiedSources.push({assetId:a.assetId,fileName:a.fileName,folder:a.folder||'root',filePath:verifiedMediaFile,sourceUrl:a.assetUrl||targetUrl,probe:probeResult,candidateEvidence});
      else phase4Diagnostics.failures.push('MediaSilo asset '+a.assetId+' had no verified playable media.');
      phase4Diagnostics.assets.push({assetId:a.assetId,fileName:a.fileName,candidateCount:candidates.size,candidateEvidence,outcome:verifiedMediaFile?'verified':'no-verified-media'});
    }
    await browser.close();
  }

  const validSources = verifiedSources.map(x => x.probe ? x : {...x, probe:runFFprobe(x.filePath)}).filter(x=>x.probe?.valid&&x.probe.duration>=10);
  if(validSources.length !== 2) throw new Error('PHASE4_SOURCE_ACCEPTANCE_FAILED: expected exactly 2 verified playable videos, got '+validSources.length);

  const results=[];
  for(const a of validSources){
    const assetOutDir=path.join(outDir,a.assetId);
    const frameDir=path.join(assetOutDir,'frames');
    const frames=extractFrames(a.filePath,frameDir,a.probe.duration,12);
    results.push({
      assetId:a.assetId,fileName:a.fileName,folder:a.folder||'root',
      durationSeconds:a.probe.duration,width:a.probe.width,height:a.probe.height,
      fileSizeBytes:a.probe.size,frameCount:frames.length,frames,
      provenance:sourceType==='googledrive'?'googledrive_real_media_ffprobe_extracted':'mediasilo_real_media_ffprobe_extracted',
      sourceType,sourceUrl:a.sourceUrl||sourceUrl
    });
    phase4Diagnostics.assets.push({assetId:a.assetId,fileName:a.fileName,duration:a.probe.duration,width:a.probe.width,height:a.probe.height,frameCount:frames.length,outcome:'verified'});
  }

  const totalFrames=results.reduce((sum,r)=>sum+r.frameCount,0);
  if(totalFrames!==24) throw new Error('Phase 4 acceptance criteria failed: expected 24 total frames, got '+totalFrames);
  const manifest={complete:true,sourceType,sourceUrl,campaignId,videoAssetCount:2,frameCount:totalFrames,results,createdAt:new Date().toISOString()};
  fs.writeFileSync(path.join(outDir,'phase4-input-manifest.json'),JSON.stringify(manifest,null,2));
  fs.writeFileSync('mediasilo-debug.json',JSON.stringify(phase4Diagnostics,null,2));
  fs.writeFileSync('mediasilo-network.log',JSON.stringify(networkLog,null,2));
  console.log('PHASE4_ARTIFACT_VALIDATION_PASS');
  console.log('Source: '+sourceType+' | Assets: 2 | Frames: '+totalFrames);
})()
