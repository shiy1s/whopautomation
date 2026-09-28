const fs = require('fs');

const MAX_DEPTH = 4;
const MAX_NODES = 120;
const FETCH_TIMEOUT_MS = 30000;
const MEDIA_EXT = /\.(mp4|mov|m4v|webm|mkv|m3u8)(?:[?#]|$)/i;

const sleep = ms => new Promise(r => setTimeout(r, ms));

function classify(url) {
  const l = String(url || '').toLowerCase();
  if (l.includes('app.mediasilo.com/review/')) return { type:'MediaSilo', role:'media_source' };
  if (l.includes('drive.google.com/drive/')) return { type:'GoogleDrive', role:'media_source' };
  if (l.includes('drive.google.com/file/')) return { type:'GoogleDriveFile', role:'media_source' };
  if (l.includes('docs.google.com/document/')) return { type:'GoogleDocs', role:'instruction' };
  if (l.includes('docs.google.com/spreadsheets/')) return { type:'GoogleSheets', role:'asset_index' };
  if (l.includes('dropbox.com/')) return { type:'Dropbox', role:'media_source' };\n  if (l.includes('we.tl/') || l.includes('wetransfer.com/')) return { type:'WeTransfer', role:'media_source' };
  if (l.includes('notion.so/') || l.includes('notion.site/') || l.includes('app.notion.com/')) return { type:'Notion', role:'instruction' };
  if (l.includes('discord.com/') || l.includes('discord.gg/')) return { type:'Discord', role:'application' };
  if (l.includes('tally.so/')) return { type:'Tally', role:'application' };
  if (l.includes('loom.com/')) return { type:'Loom', role:'instruction' };
  if (l.includes('frame.io/') || l.includes('next.frame.io/')) return { type:'NextFrame', role:'media_source' };
  if (l.includes('youtube.com/') || l.includes('youtu.be/')) return { type:'YouTube', role:'media_source' };
  if (l.includes('tiktok.com/') || l.includes('instagram.com/')) return { type:'SocialReference', role:'social_reference' };
  if (l.includes('linkin.bio/') || l.includes('linktr.ee/')) return { type:'LinkInBio', role:'instruction' };
  if (MEDIA_EXT.test(l)) return { type:'DirectFile', role:'direct_file' };
  return { type:'Website', role:'unknown' };
}

function normalizeUrl(raw, baseUrl) {
  if (!raw) return null;
  let s = String(raw).trim().replace(/&amp;/gi, '&').replace(/^['\"]|['\"]$/g, '');
  if (!s || s.startsWith('#') || /^(javascript|mailto|tel):/i.test(s)) return null;
  try {
    let u = new URL(s, baseUrl);

    // Google Docs/Sheets often expose links through google.com/url redirects.
    // Unwrap the destination before classification so the real source type is preserved.
    if (/^www\.google\.com$/i.test(u.hostname) && u.pathname === '/url' && u.searchParams.get('q')) {
      s = u.searchParams.get('q');
      u = new URL(s, baseUrl);
    }

    if (!/^https?:$/i.test(u.protocol)) return null;

    // Google Sheets CSV exports can append cell data after a URL, e.g.
    // ".../view?usp=sharing,,6". Canonicalize Drive links from their stable IDs.
    if (/^drive\.google\.com$/i.test(u.hostname)) {
      const file = u.pathname.match(/^\/file\/d\/([^/]+)/i);
      const folder = u.pathname.match(/^\/drive\/folders\/([^/]+)/i);
      if (file) return 'https://drive.google.com/file/d/' + file[1] + '/view';
      if (folder) return 'https://drive.google.com/drive/folders/' + folder[1];
    }

    u.hash = '';
    return u.href;
  } catch { return null; }
}

function extractUrls(html, baseUrl) {
  const found = new Map();
  const add = (raw, context) => {
    const u = normalizeUrl(raw, baseUrl);
    if (u && !found.has(u)) found.set(u, context || 'link');
  };
  for (const m of String(html || '').matchAll(/href\s*=\s*["']([^"']+)["']/gi)) add(m[1], 'href');
  for (const m of String(html || '').matchAll(/(?:src|data-src|data-url|data-href)\s*=\s*["']([^"']+)["']/gi)) add(m[1], 'embedded');
  for (const m of String(html || '').matchAll(/https?:\/\/[^\s<>'"\\]+/g)) add(m[0], 'plain_url');
  return [...found.entries()].map(([url, context]) => ({url, context}));
}

function stripHtml(html) {
  return String(html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

async function fetchText(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch(url, {
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        'User-Agent': 'WhopAutomationResourceDiscovery/1.0',
        'Accept': 'text/html,text/plain,text/csv,application/json,*/*'
      }
    });
    const contentType = String(r.headers.get('content-type') || '').toLowerCase();
    const body = await r.text();
    return { ok:r.ok, status:r.status, url:r.url, contentType, body };
  } finally {
    clearTimeout(timer);
  }
}

function transformUrl(url, type) {
  if (type === 'GoogleDocs') {
    return url.split('/edit')[0] + '/export?format=html';
  }
  if (type === 'GoogleSheets') {
    return url.split('/edit')[0] + '/export?format=csv';
  }
  if (type === 'Dropbox') {
    try {
      const u = new URL(url);
      if (u.searchParams.has('dl')) u.searchParams.set('dl','1');
      else u.searchParams.set('dl','1');
      return u.href;
    } catch {}
  }
  return url;
}

function scoreResource(r) {
  let s = 0;
  const t = String(r.type || '');
  const role = String(r.role || '');
  const c = String(r.context || '').toLowerCase();
  if (role === 'media_source') s += 60;
  if (role === 'direct_file') s += 100;
  if (t === 'MediaSilo') s += 50;
  if (t === 'GoogleDrive' || t === 'GoogleDriveFile') s += 45;
  if (t === 'Dropbox') s += 45;
  if (t === 'YouTube') s += 30;
  if (t === 'NextFrame') s += 45;
  if (c.includes('asset') || c.includes('footage') || c.includes('clips') || c.includes('vod') || c.includes('source')) s += 35;
  if (c.includes('profile') || c.includes('logo') || c.includes('account')) s -= 60;
  return s;
}

async function writeResult(campaignId, result) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) throw new Error('GITHUB_TOKEN missing; cannot persist resource graph.');
  const repo = process.env.GITHUB_REPOSITORY || 'shiy1s/whopautomation';
  const path = `runtime/resource-discovery/${campaignId}.json`;
  const api = `https://api.github.com/repos/${repo}/contents/${path}`;
  const content = Buffer.from(JSON.stringify(result, null, 2)).toString('base64');
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'WhopAutomationResourceDiscovery/1.0'
  };
  let sha = null;
  const existing = await fetch(api,{headers});
  if(existing.ok){
    const data=await existing.json();
    sha=data.sha||null;
  }else if(existing.status!==404){
    const detail=await existing.text().catch(()=> '');
    throw new Error(`RESOURCE_GRAPH_LOOKUP_FAILED: ${existing.status} ${detail.slice(0,500)}`);
  }
  const body={message:`resource-discovery: ${campaignId}`,content,...(sha?{sha}:{})};
  let last='';
  for(let attempt=1;attempt<=3;attempt++){
    const put=await fetch(api,{method:'PUT',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(put.ok)return path;
    last=`${put.status} ${(await put.text()).slice(0,800)}`;
    if(![409,429,500,502,503,504].includes(put.status))break;
    await sleep(1000*attempt);
  }
  throw new Error(`RESOURCE_GRAPH_PERSIST_FAILED: ${last}`);
}

(async () => {
  const campaignId = String(process.env.CAMPAIGN_ID || '').trim();
  if (!campaignId) throw new Error('CAMPAIGN_ID is required.');
  let refs = [];
  try { refs = JSON.parse(process.env.REFERENCE_URLS || '[]'); } catch { refs = []; }
  if (!Array.isArray(refs)) refs = [];
  const campaignUrl = String(process.env.CAMPAIGN_URL || '').trim();
  const seeds = [...new Set([...refs, campaignUrl].map(String).map(x=>x.trim()).filter(Boolean))];
  if (!seeds.length) throw new Error('RESOURCE_DISCOVERY_NO_SEEDS');

  const queue = seeds.map(url => ({url, depth:0, parent:null, context:'campaign_reference'}));
  const visited = new Set();
  const resources = [];
  const mediaCandidates = [];
  const failures = [];

  while (queue.length && resources.length < MAX_NODES) {
    const current = queue.shift();
    const url = normalizeUrl(current.url, current.url);
    if (!url || visited.has(url)) continue;
    visited.add(url);

    const meta = classify(url);
    const resource = {
      url,
      type:meta.type,
      role:meta.role,
      parentResource:current.parent,
      depth:current.depth,
      context:current.context,
      status:'DISCOVERED'
    };

    const transformed = transformUrl(url, meta.type);
    if (transformed !== url) resource.fetchUrl = transformed;

    const likelyMedia = meta.role === 'media_source' || meta.role === 'direct_file';
    if (likelyMedia) {
      resource.status = 'MEDIA_CANDIDATE';
      resource.score = scoreResource(resource);
      mediaCandidates.push(resource);
    }

    resources.push(resource);

    const shouldFetch = current.depth < MAX_DEPTH && (
      ['Notion','GoogleDocs','GoogleSheets','Website','Dropbox','WeTransfer','Loom','LinkInBio','Discord','Tally','NextFrame'].includes(meta.type)
    );
    if (!shouldFetch) continue;

    try {
      const response = await fetchText(transformed);
      resource.status = response.ok ? 'FETCHED' : 'FETCH_FAILED';
      resource.httpStatus = response.status;
      resource.finalUrl = response.url;
      resource.contentType = response.contentType;
      resource.textPreview = stripHtml(response.body).slice(0, 1200);

      if (!response.ok) {
        failures.push({url,type:meta.type,depth:current.depth,reason:`HTTP_${response.status}`});
        continue;
      }

      const childLinks = extractUrls(response.body, response.url);
      for (const child of childLinks) {
        const childMeta = classify(child.url);
        const context = `${meta.type}:${child.context}`;
        const childDepth = current.depth + 1;
        if (childDepth > MAX_DEPTH || visited.has(child.url)) continue;
        queue.push({url:child.url,depth:childDepth,parent:url,context});
        if (childMeta.role === 'media_source' || childMeta.role === 'direct_file') {
          const candidate = {
            url:child.url,
            type:childMeta.type,
            role:childMeta.role,
            parentResource:url,
            depth:childDepth,
            context,
            status:'MEDIA_CANDIDATE'
          };
          candidate.score = scoreResource(candidate);
          if (!mediaCandidates.some(x=>x.url===candidate.url)) mediaCandidates.push(candidate);
        }
      }
    } catch (e) {
      resource.status='FETCH_ERROR';
      resource.error=e.message;
      failures.push({url,type:meta.type,depth:current.depth,reason:e.message});
    }
    await sleep(150);
  }

  const ranked = [...new Map(mediaCandidates.map(x=>[x.url,x])).values()]
    .sort((a,b)=>Number(b.score||0)-Number(a.score||0));

  const graph = {
    schemaVersion:'1.0',
    campaignId,
    campaignUrl,
    status:ranked.length ? 'MEDIA_SOURCE_DISCOVERED' : 'NO_MEDIA_SOURCE_DISCOVERED',
    maxDepth:MAX_DEPTH,
    visitedCount:visited.size,
    resourceCount:resources.length,
    resources,
    mediaCandidates:ranked,
    primaryMediaSources:ranked.slice(0,20),
    failures,
    createdAt:new Date().toISOString()
  };

  // Always materialize the artifact before repository persistence so failed persistence is diagnosable.
  fs.mkdirSync('resource-discovery-artifact',{recursive:true});
  fs.writeFileSync('resource-discovery-artifact/resource-graph.json',JSON.stringify(graph,null,2));
  fs.writeFileSync('resource-discovery-artifact/media-sources.json',JSON.stringify(ranked,null,2));
  const resultPath = await writeResult(campaignId, graph);
  console.log('RESOURCE_DISCOVERY_COMPLETE');
  console.log(JSON.stringify({
    campaignId,
    status:graph.status,
    resources:resources.length,
    mediaCandidates:ranked.length,
    primaryMediaSources:ranked.slice(0,10).map(x=>({type:x.type,url:x.url,score:x.score,parent:x.parentResource})),
    persistedPath:resultPath
  },null,2));
})();