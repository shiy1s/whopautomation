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
  const sourceType=String(process.env.INPUT_SOURCE_TYPE||'mediasilo').trim().toLowerCase();
  const sourceUrl=String(process.env.INPUT_SOURCE_URL||'').trim();
  const campaignId=String(process.env.INPUT_CAMPAIGN_ID||'').trim();
  if(!['mediasilo','googledrive'].includes(sourceType)) throw new Error('Unsupported Phase 4 source_type: '+sourceType);
  if(!sourceUrl) throw new Error('Missing Phase 4 source_url.');

  if(sourceType==='googledrive'){
    console.log('=== Starting Phase 4 Google Drive source adapter ===');
    const outDir=path.resolve('phase4-input');
    const tmpDir=path.resolve('.tmp-media');
    fs.rmSync(outDir,{recursive:true,force:true});
    fs.rmSync(tmpDir,{recursive:true,force:true});
    fs.mkdirSync(outDir,{recursive:true});
    fs.mkdirSync(tmpDir,{recursive:true});
    try{
      if(sourceUrl.toLowerCase().includes('/drive/folders/')){
        cp.execFileSync('gdown',['--folder','--continue','--retries','3',sourceUrl,'-O',tmpDir],{stdio:'inherit'});
      }else{
        cp.execFileSync('gdown',['--continue','--retries','3',sourceUrl,'-O',path.join(tmpDir,'drive_source.mp4')],{stdio:'inherit'});
      }
    }catch(e){throw new Error('GOOGLE_DRIVE_DOWNLOAD_FAILED: '+(e.stderr?String(e.stderr):e.message));}
    const walk=dir=>{
      const out=[];
      for(const ent of fs.readdirSync(dir,{withFileTypes:true})){
        const p=path.join(dir,ent.name);
        if(ent.isDirectory()) out.push(...walk(p)); else out.push(p);
      }
      return out;
    };
    const files=walk(tmpDir).filter(p=>/\\.(mp4|mov|m4v|webm|mkv)$/i.test(p)).sort();
    if(files.length<2) throw new Error('GOOGLE_DRIVE_SOURCE_INSUFFICIENT_VIDEO: found '+files.length+' video files.');
    const selected=files.slice(0,2);
    const results=[];
    for(const filePath of selected){
      const probe=runFFprobe(filePath);
      if(!probe.valid||probe.duration<10) throw new Error('Google Drive source failed ffprobe: '+path.basename(filePath));
      const assetId='gdrive-'+require('crypto').createHash('sha256').update(sourceUrl+'|'+path.relative(tmpDir,filePath)).digest('hex').slice(0,24);
      const frameDir=path.join(outDir,assetId,'frames');
      const frames=extractFrames(filePath,frameDir,probe.duration,12);
      results.push({assetId,fileName:path.basename(filePath),folder:path.dirname(path.relative(tmpDir,filePath))||'root',durationSeconds:probe.duration,width:probe.width,height:probe.height,fileSizeBytes:probe.size,frameCount:frames.length,frames,provenance:'googledrive_real_media_ffprobe_extracted',sourceType:'GoogleDrive',sourceUrl});
    }
    const manifest={complete:true,sourceType:'GoogleDrive',sourceUrl,campaignId,videoAssetCount:2,frameCount:24,results,createdAt:new Date().toISOString()};
    fs.writeFileSync(path.join(outDir,'phase4-input-manifest.json'),JSON.stringify(manifest,null,2));
    fs.writeFileSync('mediasilo-debug.json',JSON.stringify({sourceType:'GoogleDrive',sourceUrl,campaignId,assets:results.map(x=>({assetId:x.assetId,fileName:x.fileName,duration:x.durationSeconds,frameCount:x.frameCount}))},null,2));
    fs.writeFileSync('mediasilo-network.log','Google Drive source adapter used; no MediaSilo network crawl.\n');
    console.log('PHASE4_ARTIFACT_VALIDATION_PASS');
    return;
  }

  console.log('=== Starting Phase 4 MediaSilo Real-Media Worker ===');
  // Phase 4 consumes the committed Phase 2 inventory directly.
  // Do not pass the inventory through workflow_dispatch/base64 input: that
  // introduces an unnecessary corruption vector and makes the run non-deterministic.
  const inventoryPath = path.resolve('mediasilo-inventory.json');
  if (!fs.existsSync(inventoryPath)) {
    throw new Error(`Missing committed Phase 2 inventory: ${inventoryPath}`);
  }

  const inputRaw = fs.readFileSync(inventoryPath, 'utf8');
  let inv;
  try {
    inv = JSON.parse(inputRaw);
  } catch (e) {
    throw new Error(`Invalid mediasilo-inventory.json: ${e.message}`);
  }

  const assets = (inv.assets || []).filter(a => a.type === 'video');
  if (assets.length !== 2) {
    throw new Error('Expected exactly 2 video assets in inventory, found ' + assets.length);
  }

  const outDir = path.resolve('phase4-input');
  const tmpMediaDir = path.resolve('.tmp-media');
  fs.mkdirSync(outDir, { recursive: true });
  fs.mkdirSync(tmpMediaDir, { recursive: true });

  const networkLog = [];
  const phase4Diagnostics = {
    startedAt: new Date().toISOString(),
    assets: [],
    failures: []
  };

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const mediasiloApiHeaders = {};

  context.on('request', req => {
    const h = req.headers();
    if (h['x-key']) mediasiloApiHeaders['x-key'] = h['x-key'];
    if (h['x-secret']) mediasiloApiHeaders['x-secret'] = h['x-secret'];
  });

  // Bootstrap review session to establish auth state
  const reviewUrl = inv.source?.reviewUrl || 'https://app.mediasilo.com/review/6a91d42a1c9dd13bd6636b14';
  console.log(`Bootstrapping review session at ${reviewUrl}...`);
  const initPage = await context.newPage();
  await initPage.goto(reviewUrl, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
  await sleep(3000);
  await initPage.close();

  const results = [];

  for (const a of assets) {
    console.log(`\n--- Processing asset: ${a.fileName} (${a.assetId}) ---`);
    const assetOutDir = path.join(outDir, a.assetId);
    const frameDir = path.join(assetOutDir, 'frames');
    fs.mkdirSync(frameDir, { recursive: true });
    try {

    const candidates = new Map();
    const page = await context.newPage();

    page.on('response', async response => {
      try {
        const ct = String(response.headers()['content-type'] || '').toLowerCase();
        const url = response.url();
        networkLog.push({ url, status: response.status(), contentType: ct, assetId: a.assetId });

        if (isMediaType(ct) || isMediaUrl(url)) {
          candidates.set(url, { url, contentType: ct, source: 'network-response' });
        }

        if (ct.includes('json')) {
          try {
            const jsonBody = await response.json();
            const out = new Set();
            collectStrings(jsonBody, out);
            for (const u of out) {
              if (isMediaUrl(u)) candidates.set(u, { url: u, contentType: ct, source: 'json-body' });
            }
          } catch {}
        }
      } catch {}
    });

    const targetUrl = a.assetUrl || `${reviewUrl}/${a.assetId}`;
    console.log(`Navigating to target asset URL: ${targetUrl}`);
    await page.goto(targetUrl, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
    await sleep(4000);

    // If API fallback needed, query MediaSilo API with captured headers
    if (candidates.size === 0 && mediasiloApiHeaders['x-key']) {
      console.log('Attempting MediaSilo API fallback query...');
      try {
        const apiRes = await context.request.get(
          `https://api.mediasilo.com/v3/quicklinks/6a91d42a1c9dd13bd6636b14/assets/${a.assetId}`,
          { headers: { ...mediasiloApiHeaders, Referer: reviewUrl, Accept: 'application/json' } }
        );
        if (apiRes.ok()) {
          const body = await apiRes.json();
          const found = new Set();
          collectStrings(body, found);
          for (const u of found) {
            if (isMediaUrl(u)) candidates.set(u, { url: u, contentType: 'application/json', source: 'api-fallback' });
          }
        }
      } catch (e) {
        console.warn('API fallback request failed:', e.message);
      }
    }

    await page.close();

    console.log(`Found ${candidates.size} potential media candidate URLs for ${a.fileName}`);
    let verifiedMediaFile = null;
    let probeResult = null;

    const candidateEvidence = [];
    for (const [candidateUrl, info] of candidates.entries()) {
      console.log(`Testing candidate [${info.source}]: ${candidateUrl.slice(0, 140)}...`);
      const tempPath = path.join(tmpMediaDir, `${a.assetId}_candidate_${Date.now()}.mp4`);
      try {
        const fetchRes = await context.request.get(candidateUrl, {
          timeout: 60000,
          headers: { Referer: reviewUrl, Accept: '*/*' }
        });
        const status = fetchRes.status();
        const resCt = String(fetchRes.headers()['content-type'] || '').toLowerCase();
        if (!fetchRes.ok()) {
          candidateEvidence.push({ url: candidateUrl, source: info.source, status, contentType: resCt, outcome: 'http-not-ok' });
          console.warn(`Candidate HTTP not ok: status=${status} contentType=${resCt}`);
          continue;
        }

        const buf = await fetchRes.body();
        if (buf.length < 100000) {
          candidateEvidence.push({ url: candidateUrl, source: info.source, status, contentType: resCt, bytes: buf.length, outcome: 'too-small' });
          continue; // Must be at least 100KB
        }

        fs.writeFileSync(tempPath, buf);
        const probe = runFFprobe(tempPath);
        if (probe.valid) {
          console.log(`SUCCESS: Verified playable media! Duration: ${probe.duration}s, Res: ${probe.width}x${probe.height}, Size: ${probe.size} bytes`);
          candidateEvidence.push({ url: candidateUrl, source: info.source, status, contentType: resCt, bytes: buf.length, outcome: 'verified' });
          verifiedMediaFile = tempPath;
          probeResult = probe;
          break;
        } else {
          console.warn(`Candidate failed ffprobe validation: status=${status} contentType=${resCt} bytes=${buf.length} error=${probe.error} fileHead=${probe.fileHead}`);
          candidateEvidence.push({ url: candidateUrl, source: info.source, status, contentType: resCt, bytes: buf.length, outcome: 'ffprobe-failed', error: probe.error, fileHead: probe.fileHead });
          fs.unlinkSync(tempPath);
        }
      } catch (e) {
        console.warn(`Failed to fetch/verify candidate:`, e.message);
        candidateEvidence.push({ url: candidateUrl, source: info.source, outcome: 'fetch-error', error: e.message });
        if (fs.existsSync(tempPath)) fs.unlinkSync(tempPath);
      }
    }

    if (!verifiedMediaFile || !probeResult) {
      const err = `No verified playable media response for ${a.assetId} (${a.fileName}); candidates tested=${candidates.size}`;
      phase4Diagnostics.failures.push(err);
      phase4Diagnostics.assets.push({ assetId: a.assetId, fileName: a.fileName, candidateCount: candidates.size, candidateEvidence, outcome: 'no-verified-media' });
      console.error(err);
      continue;
    }

    // Extract 12 representative frames
    console.log(`Extracting 12 representative frames for ${a.fileName}...`);
    const frames = extractFrames(verifiedMediaFile, frameDir, probeResult.duration, 12);
    console.log(`Extracted ${frames.length} valid frame files.`);

    results.push({
      assetId: a.assetId,
      fileName: a.fileName,
      folder: a.folder || 'root',
      durationSeconds: probeResult.duration,
      width: probeResult.width,
      height: probeResult.height,
      fileSizeBytes: probeResult.size,
      frameCount: frames.length,
      frames,
      provenance: 'mediasilo_real_media_ffprobe_extracted'
    });

    phase4Diagnostics.assets.push({
      assetId: a.assetId,
      fileName: a.fileName,
      duration: probeResult.duration,
      width: probeResult.width,
      height: probeResult.height,
      frameCount: frames.length,
      candidateCount: candidates.size,
      candidateEvidence,
      outcome: 'verified'
    });
    } catch (assetErr) {
      console.error(`Asset ${a.assetId} failed:`, assetErr.message);
      phase4Diagnostics.failures.push(`${a.assetId}: ${assetErr.message}`);
    }
  }

  await browser.close();

  const totalFrames = results.reduce((sum, r) => sum + r.frameCount, 0);
  if (results.length !== 2 || totalFrames !== 24) {
    throw new Error(`Phase 4 acceptance criteria failed: expected 2 assets & 24 total frames, got ${results.length} assets & ${totalFrames} frames`);
  }

  const manifest = {
    complete: true,
    videoAssetCount: results.length,
    frameCount: totalFrames,
    results,
    createdAt: new Date().toISOString()
  };

  const manifestPath = path.join(outDir, 'phase4-input-manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  fs.writeFileSync('mediasilo-debug.json', JSON.stringify(phase4Diagnostics, null, 2));
  fs.writeFileSync('mediasilo-network.log', JSON.stringify(networkLog, null, 2));

  console.log('\n==================================================');
  console.log('PHASE4_ARTIFACT_VALIDATION_PASS');
  console.log(`Assets: ${results.length} | Frames: ${totalFrames} | Manifest: ${manifestPath}`);
  console.log('==================================================\n');
})();