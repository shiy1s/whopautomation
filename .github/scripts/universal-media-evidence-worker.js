const fs=require('fs');
const path=require('path');
const cp=require('child_process');
const {chromium}=require('playwright');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const mediaExt=/\.(mp4|mov|m4v|webm|mkv)(?:[?#]|$)/i;
const isMediaUrl=u=>mediaExt.test(String(u||''))||/\.(m3u8|mpd)(?:[?#]|$)/i.test(String(u||''));
const probe=file=>{try{const out=cp.execFileSync('ffprobe',['-v','error','-show_entries','format=duration,size','-select_streams','v:0','-show_entries','stream=width,height','-of','json',file],{encoding:'utf8'});const d=JSON.parse(out),duration=Number(d.format?.duration||0),width=Number(d.streams?.[0]?.width||0),height=Number(d.streams?.[0]?.height||0),size=Number(d.format?.size||0);return{valid:duration>=10&&width>0&&height>0,duration,width,height,size};}catch(e){return{valid:false,error:e.message};}};
const frames=(file,out,duration)=>{fs.mkdirSync(out,{recursive:true});const a=[];for(let i=1;i<=12;i++){const ts=(duration*i/13).toFixed(2),name='frame_'+String(i).padStart(2,'0')+'.jpg',p=path.join(out,name);cp.execFileSync('ffmpeg',['-y','-ss',ts,'-i',file,'-vframes','1','-q:v','2',p],{stdio:'ignore'});if(!fs.existsSync(p)||fs.statSync(p).size<1000)throw new Error('frame extraction failed: '+name);a.push({frameIndex:i,fileName:name,filePath:path.relative('phase4-input',p).replace(/\\/g,'/'),timestampSeconds:Number(ts),fileSizeBytes:fs.statSync(p).size});}return a;};
const unique=a=>[...new Set(a.map(String).map(x=>x.trim()).filter(Boolean))];
async function download(url,target){const r=await fetch(url,{redirect:'follow',headers:{'User-Agent':'WhopAutomationMediaWorker/1.0','Accept':'*/*'}});if(!r.ok)throw new Error('HTTP '+r.status);const b=Buffer.from(await r.arrayBuffer());if(b.length<100000)throw new Error('response too small');fs.writeFileSync(target,b);return r.url;}
async function browserCandidates(url,options={}){const timeoutMs=Number(options.timeoutMs||45000),settleMs=Number(options.settleMs||4000);const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});const ctx=await browser.newContext({viewport:{width:1920,height:1080},userAgent:'Mozilla/5.0'});const found=new Map();const page=await ctx.newPage();page.on('response',r=>{try{const u=r.url(),ct=String(r.headers()['content-type']||'');if(isMediaUrl(u)||/^video\//i.test(ct))found.set(u,{url:u,ct});}catch{}});await page.goto(url,{waitUntil:'domcontentloaded',timeout:timeoutMs}).catch(()=>{});await sleep(settleMs);const html=await page.content().catch(()=> '');for(const m of html.matchAll(/https?:\/\/[^\s<>'"\\]+/g)){const u=m[0].replace(/[),]}]+$/,'');if(isMediaUrl(u))found.set(u,{url:u,ct:'html'});}await browser.close();return[...found.values()];}
const classifyBrowserUrl=u=>{
  const l=String(u||'').toLowerCase();
  if(l.includes('app.mediasilo.com/review/'))return'MediaSilo';
  if(l.includes('drive.google.com/drive/'))return'GoogleDrive';
  if(l.includes('drive.google.com/file/'))return'GoogleDriveFile';
  if(l.includes('dropbox.com/'))return'Dropbox';
  if(l.includes('we.tl/')||l.includes('wetransfer.com/'))return'WeTransfer';
  if(l.includes('youtube.com/')||l.includes('youtu.be/'))return'YouTube';
  if(l.includes('frame.io/')||l.includes('next.frame.io/'))return'NextFrame';
  if(mediaExt.test(l)||/\.(m3u8|mpd)(?:[?#]|$)/i.test(l))return'DirectFile';
  return'BrowserFallback';
};

async function genericBrowserDiscover(url,options={}){
  const timeoutMs=Number(options.timeoutMs||45000),settleMs=Number(options.settleMs||5000),maxPages=Number(options.maxPages||4);
  const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});
  const ctx=await browser.newContext({viewport:{width:1920,height:1080},userAgent:'Mozilla/5.0'});
  const found=new Map(),queue=[{url,depth:0}],visited=new Set();
  const add=(u,kind,depth)=>{
    if(!u||typeof u!=='string'||!/^https?:\/\//i.test(u))return;
    const clean=u.replace(/[),]}]+$/,'');
    if(clean.length<12)return;
    const known=classifyBrowserUrl(clean);
    if(isMediaUrl(clean)||known!=='BrowserFallback')found.set(clean,{url:clean,kind});
    else if(depth<2&&!visited.has(clean)&&queue.length<maxPages*8)queue.push({url:clean,depth:depth+1});
  };
  while(queue.length&&visited.size<maxPages){
    const current=queue.shift();
    if(!current||visited.has(current.url))continue;
    visited.add(current.url);
    const page=await ctx.newPage();
    page.on('response',r=>{
      try{
        const u=r.url(),ct=String(r.headers()['content-type']||'');
        if(isMediaUrl(u)||/^video\//i.test(ct)||/mpegurl|quicktime|webm|mp2t/i.test(ct))found.set(u,{url:u,kind:'network-media'});
        if(ct.toLowerCase().includes('json'))r.text().then(txt=>{
          for(const m of String(txt||'').matchAll(/https?:\/\/[^\s<>'"\\]+/g))add(m[0],'json-url',current.depth);
        }).catch(()=>{});
      }catch{}
    });
    try{
      await page.goto(current.url,{waitUntil:'domcontentloaded',timeout:timeoutMs}).catch(()=>{});
      await sleep(1500);
      await page.evaluate(()=>{
        const els=[...document.querySelectorAll('video,audio,source,iframe')];
        els.forEach(e=>{try{e.scrollIntoView({block:'center'})}catch{}});
        const buttons=[...document.querySelectorAll('button,[role="button"],a')].filter(e=>/play|watch|download|view|open|media|video|footage/i.test(String(e.innerText||e.getAttribute('aria-label')||''))).slice(0,8);
        for(const e of buttons){try{e.click()}catch{}}
      }).catch(()=>{});
      await sleep(settleMs);
      const dom=await page.evaluate(()=>{
        const out=[],add=v=>{if(v)out.push(String(v))};
        document.querySelectorAll('video,source,audio').forEach(e=>{add(e.currentSrc);add(e.src);add(e.getAttribute('data-src'));});
        document.querySelectorAll('iframe,a').forEach(e=>add(e.href||e.src));
        document.querySelectorAll('meta[property],meta[name]').forEach(e=>add(e.content));
        document.querySelectorAll('[data-url],[data-href],[data-video-url],[data-download-url]').forEach(e=>{add(e.getAttribute('data-url'));add(e.getAttribute('data-href'));add(e.getAttribute('data-video-url'));add(e.getAttribute('data-download-url'));});
        return out;
      }).catch(()=>[]);
      for(const u of dom)add(u,'dom',current.depth);
      const html=await page.content().catch(()=> '');
      for(const m of html.matchAll(/https?:\/\/[^\s<>'"\\]+/g))add(m[0],'html',current.depth);
      const perf=await page.evaluate(()=>performance.getEntriesByType('resource').map(x=>x.name)).catch(()=>[]);
      for(const u of perf)add(u,'performance',current.depth);
    }finally{await page.close().catch(()=>{});}
  }
  await browser.close();
  return [...found.values()];
}

async function downloadBrowserCandidate(candidate,target,requestContext,referer){
  const u=String(candidate?.url||candidate||'');
  if(/\.(m3u8|mpd)(?:[?#]|$)/i.test(u)){
    let headers='';
    try{
      const cookies=await requestContext.storageState();
      const cookie=(cookies.cookies||[]).map(c=>c.name+'='+c.value).join('; ');
      if(cookie)headers+='Cookie: '+cookie+'\\r\\n';
      if(referer)headers+='Referer: '+referer+'\\r\\n';
    }catch{}
    const args=['-y'];
    if(headers)args.push('-headers',headers);
    args.push('-i',u,'-c','copy',target);
    cp.execFileSync('ffmpeg',args,{stdio:'ignore'});
    return;
  }
  if(!requestContext){await download(u,target);return;}
  const response=await requestContext.get(u,{timeout:60000,headers:{Referer:referer||'https://www.google.com/',Accept:'*/*'}});
  if(!response.ok())throw new Error('HTTP '+response.status());
  const body=await response.body();
  if(body.length<100000)throw new Error('response too small');
  fs.writeFileSync(target,body);
}

async function collectSource(type,url,tmp){
  const out=[];
  const l=String(type).toLowerCase();

  if(l==='directfile'){
    const p=path.join(tmp,'direct_'+out.length+'.bin');
    await download(url,p);
    out.push({file:p,sourceUrl:url});
    return out;
  }

  if(l==='googledrive'){
    const dir=path.join(tmp,'gdrive_'+Date.now());
    fs.mkdirSync(dir,{recursive:true});

    // Folder links: enumerate first, then download only entries whose
    // reported Drive filename is a real media file. Do not crawl the folder
    // page as a generic website: Drive folders can contain shortcuts, docs,
    // YouTube references and other unrelated resources.
    if(url.toLowerCase().includes('/drive/folders/')){
      try{
        const listing=JSON.parse(cp.execFileSync('gdown',[url,'--folder','--json','--quiet'],{encoding:'utf8',maxBuffer:8*1024*1024}));
        const mediaEntries=Array.isArray(listing)
          ? listing.filter(x=>/^https?:\/\/drive\.google\.com\//i.test(String(x?.url||'')) && mediaExt.test(String(x?.path||'')))
          : [];
        console.log('Google Drive folder media entries:',mediaEntries.length,'of',Array.isArray(listing)?listing.length:0);
        for(let i=0;i<mediaEntries.length;i++){
          const entry=mediaEntries[i];
          const safeName=path.basename(String(entry.path||('drive_'+i+'.mp4')));
          const p=path.join(dir,String(i).padStart(4,'0')+'_'+safeName);
          try{
            cp.execFileSync('gdown',['--continue','--retries','2','--timeout','120',String(entry.url),'-O',p],{stdio:'inherit',timeout:150000});
            if(fs.existsSync(p)&&probe(p).valid) out.push({file:p,sourceUrl:url});
            else if(fs.existsSync(p)) fs.unlinkSync(p);
          }catch(e){
            console.warn('Google Drive folder media download failed:',entry.url,e.message);
            if(fs.existsSync(p)) fs.unlinkSync(p);
          }
        }
      }catch(e){
        console.warn('Google Drive folder listing unavailable:',e.message);
      }
      return out;
    }

    // Individual Drive file: try gdown first, then inspect the Drive viewer
    // for a real video response if direct download is quota-blocked.
    try{
      const p=path.join(dir,'source');
      cp.execFileSync('gdown',['--continue','--retries','2','--timeout','120',url,'-O',p],{stdio:'inherit',timeout:150000});
      if(fs.existsSync(p)) out.push({file:p,sourceUrl:url});
    }catch(e){
      console.warn('Google Drive direct download unavailable:',url,e.message);
    }

    if(!out.length){
      for(const candidate of await browserCandidates(url,{timeoutMs:12000,settleMs:1500})){
        const ext=isMediaUrl(candidate.url)?'.mp4':'.bin';
        const p=path.join(dir,'browser_'+Date.now()+ext);
        try{
          await download(candidate.url,p);
          if(probe(p).valid){
            out.push({file:p,sourceUrl:url});
            break;
          }
          fs.unlinkSync(p);
        }catch(e){
          if(fs.existsSync(p)) fs.unlinkSync(p);
        }
      }
    }
    return out;
  }

  if(l==='wetransfer'){
    // WeTransfer share pages are dynamic. Capture the actual downloadable/media
    // response through Chromium, then validate it with ffprobe before accepting it.
    for(const c of await browserCandidates(url,{timeoutMs:45000,settleMs:5000})){
      const p=path.join(tmp,'wetransfer_'+Date.now()+'.mp4');
      try{
        await download(c.url,p);
        if(probe(p).valid){out.push({file:p,sourceUrl:url});break;}
        fs.unlinkSync(p);
      }catch{if(fs.existsSync(p))fs.unlinkSync(p);}
    }
    return out;
  }

  if(l==='dropbox'){
    let u=url;
    try{const x=new URL(u);x.searchParams.set('dl','1');u=x.href;}catch{}
    try{
      const p=path.join(tmp,'dropbox_'+Date.now()+'.bin');
      await download(u,p);
      if(probe(p).valid) out.push({file:p,sourceUrl:url});
      else fs.unlinkSync(p);
    }catch{}
    if(!out.length){
      for(const c of await browserCandidates(url)){
        const p=path.join(tmp,'dropbox_'+Date.now()+'.mp4');
        try{
          await download(c.url,p);
          if(probe(p).valid){out.push({file:p,sourceUrl:url});break;}
          fs.unlinkSync(p);
        }catch{if(fs.existsSync(p))fs.unlinkSync(p);}
      }
    }
    return out;
  }

  if(l==='youtube'){
    const dir=path.join(tmp,'youtube_'+Date.now());
    fs.mkdirSync(dir,{recursive:true});
    cp.execFileSync('yt-dlp',['--no-playlist','-f','bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b','--merge-output-format','mp4','-o',path.join(dir,'source.%(ext)s'),url],{stdio:'inherit'});
    for(const file of fs.readdirSync(dir).map(x=>path.join(dir,x)).filter(x=>mediaExt.test(x))) out.push({file,sourceUrl:url});
    return out;
  }

  if(l==='mediasilo'||l==='nextframe'){
    for(const c of await browserCandidates(url)){
      const p=path.join(tmp,'browser_'+Date.now()+'.mp4');
      try{
        await download(c.url,p);
        if(probe(p).valid){
          out.push({file:p,sourceUrl:url});
          if(out.length>=1000) break;
        }
        fs.unlinkSync(p);
      }catch{if(fs.existsSync(p))fs.unlinkSync(p);}
    }
    return out;
  }

  if(l==='browserfallback'||l==='website'||l==='unknown'){
    const discovered=await genericBrowserDiscover(url,{maxPages:4,timeoutMs:45000,settleMs:4000});
    for(const c of discovered){
      const known=classifyBrowserUrl(c.url);
      try{
        if(known!=='BrowserFallback'&&!isMediaUrl(c.url)){
          const nested=await collectSource(known,c.url,tmp);
          for(const item of nested) out.push({...item,sourceUrl:item.sourceUrl||url,sourceAdapter:'ChromiumPlaywrightFallback'});
        }else{
          const p=path.join(tmp,'browser_fallback_'+Date.now()+'_'+out.length+(/\.(m3u8|mpd)(?:[?#]|$)/i.test(c.url)?'.mp4':'.bin'));
          await downloadBrowserCandidate(c,p,null,url);
          if(probe(p).valid)out.push({file:p,sourceUrl:url,discoveredMediaUrl:c.url,sourceAdapter:'ChromiumPlaywrightFallback'});
          else if(fs.existsSync(p))fs.unlinkSync(p);
        }
      }catch(e){
        console.warn('browser fallback candidate failed:',c.url,e.message);
        if(fs.existsSync(path.join(tmp,'browser_fallback_'+Date.now()+'_'+out.length+'.bin')))fs.unlinkSync(path.join(tmp,'browser_fallback_'+Date.now()+'_'+out.length+'.bin'));
      }
      if(out.length>=1000)break;
    }
    return out;
  }

  return out;
}
(async()=>{const defaultType=String(process.env.INPUT_SOURCE_TYPE||'').trim();let rawSources=[];try{rawSources=JSON.parse(process.env.INPUT_SOURCE_URLS||'[]')}catch{};const campaignId=String(process.env.INPUT_CAMPAIGN_ID||'').trim();const supported=['MediaSilo','GoogleDrive','GoogleDriveFile','Dropbox','WeTransfer','DirectFile','YouTube','NextFrame','Website','BrowserFallback'];const sources=rawSources.map(x=>{if(x&&typeof x==='object')return{type:String(x.type||defaultType).trim(),url:String(x.url||'').trim()};return{type:defaultType,url:String(x||'').trim()};}).filter(x=>x.type&&x.url);if(!campaignId||!sources.length)throw new Error('SOURCE_INPUT_INCOMPLETE');for(const s of sources){if(!supported.includes(s.type))console.warn('Unknown normalized source type; routing to Chromium fallback:',s.type);}fs.rmSync('phase4-input',{recursive:true,force:true});fs.rmSync('.tmp-media',{recursive:true,force:true});fs.mkdirSync('phase4-input',{recursive:true});fs.mkdirSync('.tmp-media',{recursive:true});const candidates=[];const verified=[];let googleDriveBrowserFallbacks=0;
const hasExplicitGoogleDriveFiles=sources.some(s=>{const t=String(s.type).toLowerCase(),u=String(s.url||'');return t==='googledrivefile'||(t==='googledrive'&&/drive\.google\.com\/file\//i.test(u));});
const orderedSources=[...sources].sort((a,b)=>{
  const af=String(a.type).toLowerCase()==='googledrivefile'?0:1;
  const bf=String(b.type).toLowerCase()==='googledrivefile'?0:1;
  return af-bf;
});
const processSource=async s=>{
  const isDriveFolder=String(s.type).toLowerCase()==='googledrive'&&s.url.toLowerCase().includes('/drive/folders/');
  if(isDriveFolder&&hasExplicitGoogleDriveFiles){
    console.log('Skipping redundant Google Drive folder because explicit Drive file resources are available.');
    return {discovered:[],verified:[]};
  }
  const adapterType=supported.includes(s.type)?s.type:'BrowserFallback';
  let discovered=[];
  try{
    discovered=await collectSource(adapterType,s.url,'.tmp-media');
    if(!discovered.length&&adapterType!=='BrowserFallback'&&!(String(adapterType).toLowerCase()==='googledrive'&&isDriveFolder)){
      console.warn('Primary adapter produced no media; invoking Chromium/Playwright fallback:',s.type,s.url);
      try{discovered=await collectSource('BrowserFallback',s.url,'.tmp-media');}
      catch(fallbackError){console.warn('Chromium/Playwright fallback failed:',s.url,fallbackError.message);}
    }
  }catch(e){
    console.warn('source failed',s.type,s.url,e.message);
    if(s.type!=='BrowserFallback'){
      try{discovered=await collectSource('BrowserFallback',s.url,'.tmp-media');}
      catch(fallbackError){console.warn('Chromium/Playwright fallback after adapter error failed:',s.url,fallbackError.message);}
    }
  }
  const typedDiscovered=discovered.map(x=>({...x,sourceType:x.sourceAdapter||s.type}));
  const valid=discovered.map(candidate=>{const p=probe(candidate.file);return p.valid?{...candidate,sourceType:candidate.sourceType||s.type,probe:p}:null;}).filter(Boolean);
  return {discovered:typedDiscovered,verified:valid};
};
const targetAssets=Math.max(1,Math.min(2,Number(process.env.INPUT_MAX_ASSETS||2)));
const concurrency=4;
let cursor=0;
while(cursor<orderedSources.length&&verified.length<targetAssets){
  const batch=orderedSources.slice(cursor,cursor+concurrency);
  cursor+=batch.length;
  const resultsBatch=await Promise.all(batch.map(processSource));
  for(const r of resultsBatch){
    candidates.push(...r.discovered);
    for(const v of r.verified){verified.push(v);if(verified.length>=targetAssets)break;}
  }
}

if(verified.length<1)throw new Error('SOURCE_MEDIA_INSUFFICIENT_VALID_VIDEO: '+verified.length);const results=[];
const crypto=require('crypto');
const seenAssets=new Set();
for(let i=0;i<verified.length;i++){
  const v=verified[i];
  const dedupeKey=String(v.sourceUrl||'')+'|'+path.basename(v.file);
  if(seenAssets.has(dedupeKey))continue;
  seenAssets.add(dedupeKey);
  const assetId=String(v.sourceType||defaultType||sources[0]?.type).toLowerCase()+'-'+crypto.createHash('sha256').update(dedupeKey).digest('hex').slice(0,16);
  const fd=path.join('phase4-input',assetId,'frames');
  const fr=frames(v.file,fd,v.probe.duration);
  results.push({assetId,fileName:path.basename(v.file),durationSeconds:v.probe.duration,width:v.probe.width,height:v.probe.height,fileSizeBytes:v.probe.size,frameCount:fr.length,frames:fr,provenance:'normalized_source_real_media_ffprobe_extracted',sourceType:v.sourceType||defaultType||sources[0]?.type,sourceUrl:v.sourceUrl});
}
if(!results.length)throw new Error('SOURCE_MEDIA_INSUFFICIENT_VALID_VIDEO: 0');
const manifest={complete:true,schemaVersion:'2.3',sourceType:defaultType||sources[0]?.type,sourceUrls:sources,campaignId,videoAssetCount:results.length,frameCount:results.reduce((n,x)=>n+x.frameCount,0),results,fallbackUsed:results.some(x=>String(x.sourceType||'').includes('ChromiumPlaywrightFallback')),createdAt:new Date().toISOString()};fs.writeFileSync('phase4-input/phase4-input-manifest.json',JSON.stringify(manifest,null,2));fs.writeFileSync('mediasilo-debug.json',JSON.stringify({sourceType:defaultType||sources[0]?.type,sourceUrls:sources,assets:results.map(x=>({assetId:x.assetId,fileName:x.fileName,sourceUrl:x.sourceUrl,duration:x.durationSeconds}))},null,2));fs.writeFileSync('mediasilo-network.log','Normalized source worker completed.\\n');console.log('PHASE4_ARTIFACT_VALIDATION_PASS');})();