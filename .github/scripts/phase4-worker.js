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

(async () => {
  const inputRaw = process.env.INVENTORY_JSON_B64
    ? Buffer.from(process.env.INVENTORY_JSON_B64, 'base64').toString('utf8')
    : fs.existsSync('mediasilo-inventory.json')
    ? fs.readFileSync('mediasilo-inventory.json', 'utf8')
    : null;

  if (!inputRaw) throw new Error('Missing inventory input (INVENTORY_JSON_B64 or mediasilo-inventory.json)');
  const inv = JSON.parse(inputRaw);

  const assets = (inv.assets || []).filter(a => a.type === 'video');
  if (assets.length !== 2) {
    throw new Error('Expected exactly 2 video assets, found ' + assets.length);
  }

  const outDir = path.resolve('phase4-input');
  fs.mkdirSync(outDir, { recursive: true });

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

  // Network logging and credentials capture
  context.on('request', req => {
    const h = req.headers();
    if (h['x-key']) mediasiloApiHeaders['x-key'] = h['x-key'];
    if (h['x-secret']) mediasiloApiHeaders['x-secret'] = h['x-secret'];
  });

  const results = [];

  for (const a of assets) {
    console.log(`Processing asset: ${a.fileName} (${a.assetId})`);
    const assetDir = path.join(outDir, a.assetId);
    const frameDir = path.join(assetDir, 'frames');
    fs.mkdirSync(frameDir, { recursive: true });

    const candidates = new Map();
    const directMediaBodies = [];

    const page = await context.newPage();

    page.on('response', async response => {
      try {
        const ct = String(response.headers()['content-type'] || '').toLowerCase();
        const url = response.url();
        networkLog.push({ url, status: response.status(), contentType: ct, assetId: a.assetId });

        if (ct.startsWith('video/') || isMediaUrl(url)) {
          candidates.set(url, { url, contentType: ct, source: 'network-response' });
          if (ct.startsWith('video/') && !/\.m3u8/i.test(url)) {
            directMediaBodies.push({ url, contentType: ct, response });
          }
        }

        if (ct.includes('json')) {
          try {
            const json = await response.json();
            const out = new Set();
            collectStrings(json, out);
            for (const u of out) {
              if (isMediaUrl(u)) candidates.set(u, { url: u, contentType: ct, source: 'json-body' });
            }
          } catch {}
        }
      } catch {}
    });

    try {
      // Navigate directly to the asset URL
      const targetUrl = a.assetUrl || `${inv.source.reviewUrl}/${a.assetId}`;
      console.log(`Navigating to ${targetUrl}`);
      await page.goto(targetUrl, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
      await sleep(3000);

      // Try clicking play button if available
      try {
        const playBtn = page.getByRole('button', { name: /play/i }).first();
        if (await playBtn.count()) await playBtn.click({ timeout: 5000 });
      } catch {}

      // Click video container or thumbnail if present
      try {
        const videoEl = page.locator('video, .vjs-tech, .player').first();
        if (await videoEl.count()) await videoEl.click({ timeout: 5000 });
      } catch {}
      await sleep(4000);

      // Capture DOM video sources
      const domSources = await page.evaluate(() => {
        return Array.from(document.querySelectorAll('video, source')).map(e => ({
          src: e.src || '',
          currentSrc: e.currentSrc || '',
          type: e.getAttribute('type') || ''
        }));
      });

      for (const m of domSources) {
        if (m.src && isMediaUrl(m.src)) candidates.set(m.src, { url: m.src, contentType: m.type, source: 'dom-src' });
        if (m.currentSrc && isMediaUrl(m.currentSrc)) candidates.set(m.currentSrc, { url: m.currentSrc, contentType: m.type, source: 'dom-currentSrc' });
      }

      // Same-session MediaSilo API fallback using captured auth headers
      if (inv.source && inv.source.reviewId) {
        const apiCtx = page.context().request;
        const apiUrl = `https://api.mediasilo.com/v3/quicklinks/${inv.source.reviewId}/assets/${a.assetId}`;
        try {
          const ar = await apiCtx.get(apiUrl, {
            headers: {
              ...mediasiloApiHeaders,
              Referer: inv.source.reviewUrl,
              Accept: 'application/json'
            },
            timeout: 20000
          });
          if (ar.ok()) {
            const bodyText = await ar.text();
            console.log(`PHASE4_API_FALLBACK assetId=${a.assetId} status=${ar.status()} bytes=${bodyText.length}`);
            try {
              const parsed = JSON.parse(bodyText);
              const discovered = new Set();
              collectStrings(parsed, discovered);
              for (const u of discovered) {
                if (isMediaUrl(u)) candidates.set(u, { url: u, contentType: 'api-response', source: 'same-session-api' });
              }
            } catch {}
          }
        } catch (apiErr) {
          console.error(`PHASE4_API_FALLBACK_FAILED assetId=${a.assetId} err=${apiErr.message}`);
        }
      }

      const mp = path.join(assetDir, 'source.mp4');

      // 1. Try direct video bodies captured during response interception
      for (const item of directMediaBodies) {
        try {
          const body = await item.response.body();
          if (body && body.length > 100000) {
            fs.writeFileSync(mp, body);
            console.log(`Saved ${body.length} bytes from direct response body: ${item.url}`);
            break;
          }
        } catch {}
      }

      // 2. Download candidate URLs if mp doesn't exist yet
      if (!fs.existsSync(mp) || fs.statSync(mp).size < 100000) {
        const cookies = await context.cookies();
        const cookieStr = cookies.map(c => `${c.name}=${c.value}`).join('; ');
        const headers = { Referer: inv.source.reviewUrl, Cookie: cookieStr };

        for (const item of candidates.values()) {
          try {
            if (/\.m3u8(?:[?#]|$)/i.test(item.url) || /mpegurl/i.test(item.contentType)) {
              console.log(`Downloading HLS stream with ffmpeg: ${item.url}`);
              cp.execFileSync('ffmpeg', [
                '-y', '-loglevel', 'error',
                '-headers', `Referer: ${inv.source.reviewUrl}\r\nCookie: ${cookieStr}`,
                '-i', item.url, '-c', 'copy', mp
              ], { timeout: 120000 });
            } else {
              console.log(`Downloading direct media URL: ${item.url}`);
              const r = await context.request.get(item.url, { headers, failOnStatusCode: false, timeout: 120000 });
              if (r.ok()) {
                const body = await r.body();
                if (body && body.length > 100000) {
                  fs.writeFileSync(mp, body);
                }
              }
            }

            if (fs.existsSync(mp) && fs.statSync(mp).size > 100000) {
              console.log(`Successfully acquired playable media (${fs.statSync(mp).size} bytes)`);
              break;
            }
          } catch (dlErr) {
            console.error(`Download candidate failed: ${item.url} - ${dlErr.message}`);
          }
        }
      }

      if (!fs.existsSync(mp) || fs.statSync(mp).size < 100000) {
        throw new Error(`No verified playable media response for ${a.assetId}; candidateCount=${candidates.size}`);
      }

      // ffprobe validation
      const durationStr = cp.execFileSync('ffprobe', [
        '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', mp
      ], { encoding: 'utf8' }).trim();

      const duration = Number(durationStr);
      if (!Number.isFinite(duration) || duration < 10) {
        throw new Error(`Invalid downloaded video duration for ${a.assetId}: ${durationStr}`);
      }
      console.log(`ffprobe verified duration: ${duration}s`);

      // Extract 12 frames per video
      const frames = [];
      for (let i = 0; i < 12; i++) {
        const t = Math.min(duration - 0.25, Math.max(0.25, (duration - 0.5) * (i / 11) + 0.25));
        const ts = t.toFixed(3);
        const fileName = `frame_${String(i + 1).padStart(2, '0')}_${ts.replace('.', 'p')}s.jpg`;
        const filePath = path.join(frameDir, fileName);

        cp.execFileSync('ffmpeg', [
          '-y', '-loglevel', 'error',
          '-ss', String(t), '-i', mp,
          '-frames:v', '1', '-q:v', '2', filePath
        ]);

        if (!fs.existsSync(filePath) || fs.statSync(filePath).size < 1000) {
          throw new Error(`Frame extraction failed for ${a.assetId} at timestamp ${ts}s`);
        }

        frames.push({
          index: i + 1,
          timestampSeconds: Number(ts),
          path: filePath,
          sizeBytes: fs.statSync(filePath).size
        });
      }

      results.push({
        assetId: a.assetId,
        fileName: a.fileName,
        inventoryDurationSeconds: Number(a.duration || 0) / 1000,
        durationSeconds: duration,
        frameCount: frames.length,
        frames
      });

    } finally {
      await page.close();
    }
  }

  await browser.close();

  const totalFrames = results.reduce((n, r) => n + r.frameCount, 0);
  if (results.length !== assets.length || totalFrames !== 24) {
    throw new Error(`PHASE4_INPUT_VALIDATION_FAILED: assets=${results.length} frames=${totalFrames}`);
  }

  fs.writeFileSync('mediasilo-network.log', networkLog.map(x => JSON.stringify(x)).join('\n') + '\n');
  fs.writeFileSync('mediasilo-debug.json', JSON.stringify(phase4Diagnostics, null, 2));

  fs.writeFileSync(path.join(outDir, 'phase4-input-manifest.json'), JSON.stringify({
    schemaVersion: '1.1',
    complete: true,
    videoAssetCount: results.length,
    frameCount: totalFrames,
    results,
    generatedAt: new Date().toISOString()
  }, null, 2));

  console.log(`PHASE4_INPUT_VALIDATION_PASS assets=${results.length} frames=${totalFrames}`);
})().catch(e => {
  console.error('PHASE4_WORKER_FAILED:', e);
  process.exit(1);
});
