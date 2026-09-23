const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const { chromium } = require('playwright');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const isMediaUrl = u => typeof u === 'string' && (/\.(mp4|m3u8|mov|webm)(?:[?#]|$)/i.test(u) || /video\//i.test(u));
const isMediaType = ct => typeof ct === 'string' && (/^video\//i.test(ct) || /mpegurl|quicktime|webm/i.test(ct));

const collectStrings = (obj, out = new Set()) => {
  if (!obj) return out;
  if (typeof obj === 'string') {
    if (/^https?:\/\//i.test(obj) && isMediaUrl(obj)) out.add(obj);
  } else if (Array.isArray(obj)) {
    for (const x of obj) collectStrings(x, out);
  } else if (typeof obj === 'object') {
    for (const k of Object.keys(obj)) collectStrings(obj[k], out);
  }
  return out;
};

const probe = file => {
  const raw = cp.execFileSync('ffprobe', ['-v','error','-show_entries','format=duration,size','-show_entries','stream=codec_type,width,height','-of','json',file], {encoding:'utf8'});
  const j = JSON.parse(raw);
  const v = (j.streams || []).find(x => x.codec_type === 'video');
  return {duration: Number(j.format?.duration || 0), size: Number(j.format?.size || 0), width: Number(v?.width || 0), height: Number(v?.height || 0)};
};

(async () => {
  const plan = JSON.parse(fs.readFileSync('phase6-source/phase6-clip-plan/render-plan.json','utf8'));
  const inventory = JSON.parse(fs.readFileSync('mediasilo-inventory.json','utf8'));
  const reviewUrl = inventory.source.reviewUrl;
  const assets = new Map((inventory.assets || []).filter(a => a.type === 'video').map(a => [a.assetId, a]));

  if (!Array.isArray(plan.clipPlans) || plan.clipPlans.length !== 2) throw new Error('Phase 7 requires exactly 2 clip plans');

  fs.mkdirSync('sources', {recursive:true});
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});
  const context = await browser.newContext({
    viewport:{width:1920,height:1080},
    userAgent:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
  });
  const apiHeaders = {};
  context.on('request', req => {
    const h = req.headers();
    if (h['x-key']) apiHeaders['x-key'] = h['x-key'];
    if (h['x-secret']) apiHeaders['x-secret'] = h['x-secret'];
  });

  const boot = await context.newPage();
  await boot.goto(reviewUrl,{waitUntil:'networkidle',timeout:45000}).catch(()=>{});
  await sleep(3000);
  await boot.close();

  const results = [];
  for (const p of plan.clipPlans) {
    const asset = assets.get(p.assetId);
    if (!asset) throw new Error('Asset missing from inventory: '+p.assetId);
    const page = await context.newPage();
    const candidates = new Map();

    page.on('response', async response => {
      try {
        const url=response.url();
        const ct=String(response.headers()['content-type']||'').toLowerCase();
        if (isMediaType(ct) || isMediaUrl(url)) candidates.set(url,{url,source:'network-response',contentType:ct});
        if (ct.includes('json')) {
          try {
            const body=await response.json();
            for (const u of collectStrings(body)) candidates.set(u,{url:u,source:'json-body',contentType:ct});
          } catch {}
        }
      } catch {}
    });

    await page.goto(asset.assetUrl,{waitUntil:'networkidle',timeout:45000}).catch(()=>{});
    await sleep(4000);

    if (!candidates.size && apiHeaders['x-key']) {
      try {
        const api=await context.request.get(`https://api.mediasilo.com/v3/quicklinks/${inventory.source.reviewId}/assets/${p.assetId}`,{
          headers:{...apiHeaders,Referer:reviewUrl,Accept:'application/json'},timeout:60000
        });
        if (api.ok()) for (const u of collectStrings(await api.json())) candidates.set(u,{url:u,source:'api-fallback',contentType:'application/json'});
      } catch {}
    }
    await page.close();

    let verified=null, evidence=[];
    for (const [url,info] of candidates) {
      try {
        const res=await context.request.get(url,{timeout:120000,headers:{Referer:reviewUrl,Accept:'*/*'}});
        const ct=String(res.headers()['content-type']||'').toLowerCase();
        if (!res.ok()) { evidence.push({url,status:res.status(),outcome:'http-not-ok'}); continue; }
        const buf=await res.body();
        if (buf.length < 100000) { evidence.push({url,status:res.status(),bytes:buf.length,outcome:'too-small'}); continue; }
        const tmp=path.join('sources',`.${p.assetId}.candidate.mp4`);
        fs.writeFileSync(tmp,buf);
        let pr;
        try { pr=probe(tmp); } catch(e) { evidence.push({url,bytes:buf.length,outcome:'ffprobe-failed',error:e.message}); fs.unlinkSync(tmp); continue; }
        if (pr.duration >= 10 && pr.width > 0 && pr.height > 0) {
          verified={tmp,pr,url,contentType:ct,bytes:buf.length};
          evidence.push({url,bytes:buf.length,outcome:'verified',duration:pr.duration,width:pr.width,height:pr.height});
          break;
        }
        fs.unlinkSync(tmp);
      } catch(e) { evidence.push({url,outcome:'fetch-error',error:e.message}); }
    }

    if (!verified) throw new Error(`No verified playable MediaSilo media for ${p.assetId}; candidates=${candidates.size}`);
    const finalPath=path.join('sources',`${p.assetId}.mp4`);
    fs.renameSync(verified.tmp,finalPath);
    const finalProbe=probe(finalPath);
    if (Math.abs(finalProbe.duration - Number(p.sourceDurationSeconds)) > 1.0) {
      throw new Error(`Source duration mismatch for ${p.assetId}: inventory plan=${p.sourceDurationSeconds}, downloaded=${finalProbe.duration}`);
    }
    results.push({assetId:p.assetId,fileName:p.fileName,path:finalPath,durationSeconds:finalProbe.duration,width:finalProbe.width,height:finalProbe.height,bytes:finalProbe.size,mediaUrl:verified.url,candidateEvidence:evidence});
  }

  await browser.close();
  fs.writeFileSync('phase7-source-manifest.json',JSON.stringify({complete:true,source:'MediaSilo real media',assets:results},null,2));
  console.log('PHASE7_SOURCE_VALIDATION_PASS');
  for (const r of results) console.log(r.assetId,r.fileName,r.durationSeconds,r.bytes);
})().catch(e=>{ console.error(e.stack||e); process.exit(1); });
