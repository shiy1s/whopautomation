const fs = require('fs');
const phase4Diagnostics = { startedAt: new Date().toISOString(), assets: [], network: [], failures: [], events: [] };
const networkLog = [];

(async () => {
  const fs = require('fs');
  const cp = require('child_process');
  const path = require('path');
  const { chromium } = require('playwright');

  const inv = JSON.parse(Buffer.from(process.env.INVENTORY_JSON_B64, 'base64').toString('utf8'));
  const assets = (inv.assets || []).filter(a => a.type === 'video' || /\.(mp4|mov|m4v|webm)$/i.test(String(a.fileName || '')));
  if (assets.length !== 2) throw new Error('PHASE4_SOURCE_VALIDATION_FAILED: expected 2 verified video assets, found ' + assets.length);

  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage']});
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  

  const normalize = v => {
    if (typeof v !== 'string' || !v) return null;
    let x = v.replace(/\\u0026/g, '&').trim();
    try { x = decodeURIComponent(x); } catch {}
    return /^https?:\/\//i.test(x) ? x : null;
  };

    const isMediaType = ct => /^(video\/|audio\/)/i.test(ct) || /mpegurl|quicktime|webm|mp2t/i.test(ct);
  const isMediaUrl = u => /\.(mp4|m3u8|mov|m4v|webm)(?:[?#]|$)/i.test(u || '');

  const collectStrings = (value, out) => {
    if (typeof value === 'string') {
      const u = normalize(value);
      if (u && (isMediaUrl(u) || /media|video|stream|download|cdn/i.test(u))) out.add(u);
    } else if (Array.isArray(value)) {
      for (const v of value) collectStrings(v, out);
    } else if (value && typeof value === 'object') {
      for (const v of Object.values(value)) collectStrings(v, out);
    }
  };

  const mediasiloApiHeaders={};
  const bootstrapPage=await context.newPage();
  bootstrapPage.on('request', request => { try { const u=request.url(); if(u.startsWith('https://api.mediasilo.com/')) { const h=request.headers(); if(h['x-key']&&h['x-secret']) Object.assign(mediasiloApiHeaders,{'x-key':h['x-key'],'x-secret':h['x-secret']}); } } catch {} });
  await bootstrapPage.goto(inv.source.reviewUrl,{waitUntil:'domcontentloaded',timeout:90000});
  await sleep(1500);
  const bootstrapVideo=assets.find(a=>a.type==='video');
  if(bootstrapVideo&&bootstrapVideo.folderId){
    await bootstrapPage.goto(inv.source.reviewUrl+'/f/'+bootstrapVideo.folderId,{waitUntil:'domcontentloaded',timeout:90000});
    await sleep(3000);
  }
  await bootstrapPage.close();
  if(!mediasiloApiHeaders['x-key']||!mediasiloApiHeaders['x-secret']) console.warn('PHASE4_AUTH_HEADERS_NOT_CAPTURED');
  
  for (const a of assets) {
    const dir = path.join('phase4-input', a.assetId);
    const frameDir = path.join(dir, 'frames');
    fs.mkdirSync(frameDir, { recursive: true });

    const page = await context.newPage();
    const candidates = new Map();
    const directMediaBodies = [];

    const record = (url, ct, source, meta = {}) => {
      const u = normalize(url);
      if (!u) return;
      const type = String(ct || '').toLowerCase();
      const entry = { url: u, contentType: type, source, ...meta, capturedAt: new Date().toISOString() };
      networkLog.push(entry);
      if (isMediaUrl(u) || isMediaType(type)) candidates.set(u, entry);
    };

    page.on('request', request => { try { const u = request.url(); const rt = request.resourceType(); if (rt === 'media' || isMediaUrl(u) || u.includes(a.assetId)) record(u, '', 'network-request'); } catch {} });

    page.on('requestfailed', request => { try { phase4Diagnostics.events.push({type:'requestfailed',url:request.url(),resourceType:request.resourceType(),failure:request.failure(),at:new Date().toISOString()}); } catch {} });

    page.on('requestfailed', request => { try { console.error('PHASE4_REQUEST_FAILED', JSON.stringify({url:request.url(),resourceType:request.resourceType(),failure:request.failure()})); } catch {} });

    page.on('response', async response => {
      try {
        const headers = response.headers();
        const ct = String(headers['content-type'] || '').toLowerCase();
        const contentLength = Number(headers['content-length'] || 0);
        record(response.url(), ct, 'network-response', { status: response.status(), contentLength, contentRange: headers['content-range'] || null });
        if (ct.startsWith('video/') && !/\.m3u8/i.test(response.url())) {
          directMediaBodies.push({ url: response.url(), contentType: ct, response });
        }
        if (ct.includes('json')) { try { const out = new Set(); collectStrings(await response.json(), out); for (const u of out) record(u, ct, 'json'); } catch {} }
      } catch {}
    });

    try {
      // Open the actual asset view through its parent folder so the player is mounted.
      const marker = '/' + a.assetId + '/f/';
      const parts = String(a.assetUrl || '').split(marker);
      const folderUrl = parts.length === 2 ? parts[0] + '/f/' + parts[1] : a.assetUrl;

      try {
        const link = page.locator('a[href*="' + a.assetId + '"]').first();
        if (await link.count()) await link.click({ timeout: 8000 });
      } catch {}
      try {
        const text = page.getByText(a.fileName, { exact: true }).first();
        if (await text.count()) await text.click({ timeout: 8000 });
      } catch {}
      await sleep(5000);

      try {
        const play = page.getByRole('button', { name: /play/i }).first();
        if (await play.count()) await play.click({ timeout: 5000 });
      } catch {}
      await sleep(5000);

      const dom = await page.evaluate(() => Array.from(document.querySelectorAll('video,source')).map(e => ({
        src: e.src || '',
        currentSrc: e.currentSrc || '',
        type: e.getAttribute('type') || ''
      })));
      for (const m of dom) { record(m.src, m.type, 'dom-src'); record(m.currentSrc, m.type, 'dom-currentSrc'); }

      for (const u of await page.evaluate(() => performance.getEntriesByType('resource').map(e => e.name))) record(u, '', 'performance');

      const frameState = page.frames().map(f => ({ url: f.url(), name: f.name() }));
      phase4Diagnostics.assets.push({ assetId: a.assetId, fileName: a.fileName, assetUrl: a.assetUrl, pageUrl: page.url(), frames: frameState, dom, performanceResources: await page.evaluate(() => performance.getEntriesByType('resource').map(e => e.name)), candidateCount: candidates.size, candidates: Array.from(candidates.values()) });

      const mp = path.join(dir, 'source.mp4');
      const cookie = (await context.cookies()).map(c => c.name + '=' + c.value).join('; ');
      const headers = { Referer: inv.source.reviewUrl, Cookie: cookie };

      // Direct media response bodies are the most authoritative source when MediaSilo uses signed CDN URLs.
      for (const item of directMediaBodies) {
        try {
          const body = await item.response.body();
          if (body.length > 100000) {
            fs.writeFileSync(mp, body);
            break;
          }
        } catch {}
      }

      let sourceUrl = directMediaBodies.length ? directMediaBodies[0].url : null;

      // Otherwise download a discovered HLS or direct-media URL with the authenticated browser context.
      const apiCtx=page.context().request; const apiUrl='https://api.mediasilo.com/v3/quicklinks/'+inv.source.reviewId+'/assets/'+a.assetId; try { const ar=await apiCtx.get(apiUrl,{headers:{...mediasiloApiHeaders,Referer:inv.source.reviewUrl,Accept:'application/json'},timeout:30000}); const act=String(ar.headers()['content-type']||'').toLowerCase(); const body=await ar.text(); console.log('PHASE4_API_FALLBACK',JSON.stringify({assetId:a.assetId,status:ar.status(),contentType:act,bytes:body.length,authHeadersCaptured:Boolean(mediasiloApiHeaders['x-key']&&mediasiloApiHeaders['x-secret'])})); if(ar.ok()&&body){try{const parsed=JSON.parse(body);const discovered=new Set();collectStrings(parsed,discovered);for(const u of discovered)record(u,act,'same-session-api',{status:ar.status()});}catch{}} } catch(err) { console.error('PHASE4_API_FALLBACK_FAILED',JSON.stringify({assetId:a.assetId,error:String(err&&err.message||err)})); }
      if (!fs.existsSync(mp) || fs.statSync(mp).size < 100000) {
        for (const item of [...candidates.values()]) {
          try {
            if (/\.m3u8(?:[?#]|$)/i.test(item.url) || /mpegurl/i.test(item.contentType)) {
              cp.execFileSync('ffmpeg', [
                '-y', '-loglevel', 'error',
                '-headers', 'Referer: ' + inv.source.reviewUrl + '\r\nCookie: ' + cookie,
                '-i', item.url, '-c', 'copy', mp
              ]);
            } else {
              const r = await context.request.get(item.url, { headers, failOnStatusCode: false, timeout: 120000 });
              if (r.ok()) {
                const body = await r.body();
                const ct = String(r.headers()['content-type'] || '').toLowerCase();
                if (body.length > 100000 && (ct.startsWith('video/') || isMediaUrl(item.url))) fs.writeFileSync(mp, body);
              }
            }
            if (fs.existsSync(mp) && fs.statSync(mp).size > 100000) {
              sourceUrl = item.url;
              break;
            }
          } catch {}
        }
      }

      if (!fs.existsSync(mp) || fs.statSync(mp).size < 100000) {
        throw new Error('No verified playable media response for ' + a.assetId + '; candidates=' + JSON.stringify(Array.from(candidates.values())));
      }

      const duration = Number(cp.execFileSync('ffprobe', [
        '-v','error','-show_entries','format=duration',
        '-of','default=noprint_wrappers=1:nokey=1', mp
      ], { encoding: 'utf8' }).trim());
      if (!Number.isFinite(duration) || duration < 10) throw new Error('Invalid downloaded duration: ' + duration);

      const frames = [];
      for (let i = 0; i < 12; i++) {
        const t = Math.min(duration - 0.25, Math.max(0.25, (duration - 0.5) * (i / 11) + 0.25));
        const ts = t.toFixed(3);
        const file = path.join(frameDir, 'frame_' + String(i + 1).padStart(2,'0') + '_' + ts.replace('.','p') + 's.jpg');
        cp.execFileSync('ffmpeg', ['-y','-loglevel','error','-ss',String(t),'-i',mp,'-frames:v','1','-q:v','2',file]);
        if (!fs.existsSync(file) || fs.statSync(file).size < 1000) throw new Error('Frame extraction failed at ' + ts + 's');
        frames.push({ index:i+1, timestampSeconds:Number(ts), path:file, sizeBytes:fs.statSync(file).size });
      }

      results.push({
        assetId:a.assetId,
        fileName:a.fileName,
        inventoryDurationSeconds:Number(a.duration || 0)/1000,
        durationSeconds:duration,
        sourceMediaUrl:sourceUrl,
        candidateCount:candidates.size,
        frameCount:frames.length,
        frames
      });
    } finally {
      await page.close();
    }
  }

  await browser.close();

  const totalFrames = results.reduce((n,r) => n + r.frameCount, 0);
  if (results.length !== assets.length || totalFrames !== 24) {
    throw new Error('PHASE4_INPUT_VALIDATION_FAILED: assets=' + results.length + ' frames=' + totalFrames);
  }

  fs.writeFileSync('mediasilo-network.log', networkLog.map(x => JSON.stringify(x)).join('\n') + '\n');
  fs.writeFileSync('mediasilo-debug.json', JSON.stringify(phase4Diagnostics, null, 2));

  fs.writeFileSync('phase4-input/phase4-input-manifest.json', JSON.stringify({
    schemaVersion:'1.1',
    complete:true,
    videoAssetCount:results.length,
    frameCount:totalFrames,
    results,
    generatedAt:new Date().toISOString()
  }, null, 2));

  console.log('PHASE4_INPUT_VALIDATION_PASS assets=' + results.length + ' frames=' + totalFrames);
})().catch(e => {
  try { phase4Diagnostics.failures.push({ message: String(e && e.message || e), stack: String(e && e.stack || '') }); phase4Diagnostics.finishedAt = new Date().toISOString(); fs.writeFileSync('mediasilo-network.log', networkLog.map(x => JSON.stringify(x)).join('\n') + '\n'); fs.writeFileSync('mediasilo-debug.json', JSON.stringify(phase4Diagnostics, null, 2)); } catch (diagErr) { console.error('PHASE4_DIAGNOSTICS_WRITE_FAILED', diagErr); }
  console.error(e);
  process.exit(1);
});