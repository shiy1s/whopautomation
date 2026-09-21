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
    if (obj.startsWith('http') || isMediaUrl(obj)) out.add(obj);
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
    return { valid: false, error: e.message };
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
  console.log('=== Starting Phase 4 MediaSilo Real-Media Worker ===');
  const inputRaw = process.env.INVENTORY_JSON_B64
    ? Buffer.from(process.env.INVENTORY_JSON_B64, 'base64').toString('utf8')
    : fs.existsSync('mediasilo-inventory.json')
    ? fs.readFileSync('mediasilo-inventory.json', 'utf8')
    : null;

  if (!inputRaw) throw new Error('Missing inventory input (INVENTORY_JSON_B64 or mediasilo-inventory.json)');
  const inv = JSON.parse(inputRaw);

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

    for (const [candidateUrl, info] of candidates.entries()) {
      console.log(`Testing candidate [${info.source}]: ${candidateUrl.slice(0, 100)}...`);
      const tempPath = path.join(tmpMediaDir, `${a.assetId}_candidate_${Date.now()}.mp4`);
      try {
        const fetchRes = await context.request.get(candidateUrl, { timeout: 60000 });
        if (!fetchRes.ok()) continue;
        const buf = await fetchRes.body();
        if (buf.length < 100000) continue; // Must be at least 100KB

        fs.writeFileSync(tempPath, buf);
        const probe = runFFprobe(tempPath);
        if (probe.valid) {
          console.log(`SUCCESS: Verified playable media! Duration: ${probe.duration}s, Res: ${probe.width}x${probe.height}, Size: ${probe.size} bytes`);
          verifiedMediaFile = tempPath;
          probeResult = probe;
          break;
        } else {
          console.warn(`Candidate failed ffprobe validation:`, probe.error || 'invalid metadata');
          fs.unlinkSync(tempPath);
        }
      } catch (e) {
        console.warn(`Failed to fetch/verify candidate:`, e.message);
        if (fs.existsSync(tempPath)) fs.unlinkSync(tempPath);
      }
    }

    if (!verifiedMediaFile || !probeResult) {
      const err = `No verified playable media response for ${a.assetId} (${a.fileName}); candidates tested=${candidates.size}`;
      phase4Diagnostics.failures.push(err);
      throw new Error(err);
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
      frameCount: frames.length
    });
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