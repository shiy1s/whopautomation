const fs=require('fs');
const path=require('path');
const cp=require('child_process');
const { normalizeSource, normalizeSources } = require('./media-source-contract');
const { collectDrive } = require('./google-drive-media');
const { pipeline } = require('node:stream/promises');
const { Readable, Transform } = require('node:stream');
const chromium = { launch: (...args) => require('playwright').chromium.launch(...args) };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const mediaExt=/\.(mp4|mov|m4v|webm|mkv)(?:[?#]|$)/i;
const isMediaUrl=u=>mediaExt.test(String(u||''))||/\.(m3u8|mpd)(?:[?#]|$)/i.test(String(u||''));
const probe=file=>{try{const out=cp.execFileSync('ffprobe',['-v','error','-show_entries','format=duration,size','-select_streams','v:0','-show_entries','stream=width,height','-of','json',file],{encoding:'utf8',timeout:15000,killSignal:'SIGKILL'});const d=JSON.parse(out),duration=Number(d.format?.duration||0),width=Number(d.streams?.[0]?.width||0),height=Number(d.streams?.[0]?.height||0),size=Number(d.format?.size||0);return{valid:duration>=10&&width>0&&height>0,duration,width,height,size};}catch(e){return{valid:false,error:e.message};}};
const frames=(file,out,duration)=>{fs.mkdirSync(out,{recursive:true});const a=[];for(let i=1;i<=12;i++){const ts=(duration*i/13).toFixed(2),name='frame_'+String(i).padStart(2,'0')+'.jpg',p=path.join(out,name);cp.execFileSync('ffmpeg',['-y','-ss',ts,'-i',file,'-vframes','1','-q:v','2',p],{stdio:'ignore',timeout:45000,killSignal:'SIGKILL'});if(!fs.existsSync(p)||fs.statSync(p).size<1000)throw new Error('frame extraction failed: '+name);a.push({frameIndex:i,fileName:name,filePath:path.relative('phase4-input',p).replace(/\\/g,'/'),timestampSeconds:Number(ts),fileSizeBytes:fs.statSync(p).size});}return a;};
const unique=a=>[...new Set(a.map(String).map(x=>x.trim()).filter(Boolean))];
async function download(url,target){
  const signal=AbortSignal.timeout(150000);
  try {
    const r=await fetch(url,{redirect:'follow',signal,headers:{'User-Agent':'WhopAutomationMediaWorker/1.0','Accept':'*/*'}});
    if(!r.ok)throw new Error('HTTP '+r.status);
    if(/text\/html|application\/json/i.test(r.headers.get('content-type')||''))throw new Error('NON_MEDIA_RESPONSE');
    let bytes=0;
    const limit=new Transform({transform(chunk,encoding,callback){
      bytes+=chunk.length;
      callback(bytes>2*1024*1024*1024?new Error('MEDIA_DOWNLOAD_TOO_LARGE'):null,chunk);
    }});
    await pipeline(Readable.fromWeb(r.body),limit,fs.createWriteStream(target),{signal});
    if(bytes<1000)throw new Error('response too small');
    return r.url;
  }catch(error){if(fs.existsSync(target))fs.unlinkSync(target);throw error;}
}
async function browserCandidates(url,options={}){const timeoutMs=Number(options.timeoutMs||45000),settleMs=Number(options.settleMs||4000);const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});const ctx=await browser.newContext({viewport:{width:1920,height:1080},userAgent:'Mozilla/5.0'});const found=new Map();const page=await ctx.newPage();page.on('response',r=>{try{const u=r.url(),ct=String(r.headers()['content-type']||'');if(isMediaUrl(u)||/^video\//i.test(ct))found.set(u,{url:u,ct});}catch{}});await page.goto(url,{waitUntil:'domcontentloaded',timeout:timeoutMs}).catch(()=>{});await sleep(settleMs);const html=await page.content().catch(()=> '');for(const m of html.matchAll(/https?:\/\/[^\s<>'"\\]+/g)){const u=m[0].replace(/[),]}]+$/,'');if(isMediaUrl(u))found.set(u,{url:u,ct:'html'});}await browser.close();return[...found.values()];}
const classifyBrowserUrl=u=>{try{return normalizeSource('',u).type;}catch{return null;}};

async function genericBrowserDiscover(url,options={}){
  if(normalizeSource('',url).type==='GoogleDrive')throw new Error('GOOGLE_DRIVE_BROWSER_FALLBACK_FORBIDDEN');
  const timeoutMs=Number(options.timeoutMs||45000),settleMs=Number(options.settleMs||5000),maxPages=Number(options.maxPages||4);
  const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-setuid-sandbox']});
  const ctx=await browser.newContext({viewport:{width:1920,height:1080},userAgent:'Mozilla/5.0'});
  const found=new Map(),queue=[{url,depth:0}],visited=new Set();
  const add=(u,kind,depth)=>{
    if(!u||typeof u!=='string'||!/^https?:\/\//i.test(u))return;
    const clean=u.replace(/[),]}]+$/,'');
    if(clean.length<12)return;
    const known=classifyBrowserUrl(clean);
    if(!known)return;
    if(isMediaUrl(clean)||!['BrowserFallback','Website'].includes(known))found.set(clean,{url:clean,kind});
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
    let headers=referer?'Referer: '+referer+'\r\n':'';
    try{
      const cookies=await requestContext.storageState();
      const cookie=(cookies.cookies||[]).map(c=>c.name+'='+c.value).join('; ');
      if(cookie)headers+='Cookie: '+cookie+'\r\n';
    }catch{}
    const args=['-y'];
    if(headers)args.push('-headers',headers);
    args.push('-i',u,'-c','copy',target);
    cp.execFileSync('ffmpeg',args,{stdio:'ignore',timeout:150000,killSignal:'SIGKILL'});
    return;
  }
  if(!requestContext){await download(u,target);return;}
  const response=await requestContext.get(u,{timeout:60000,headers:{Referer:referer||'https://www.google.com/',Accept:'*/*'}});
  if(!response.ok())throw new Error('HTTP '+response.status());
  const body=await response.body();
  if(body.length<100000)throw new Error('response too small');
  fs.writeFileSync(target,body);
}

async function collectSource(type,url,tmp,options={}){
  const source=normalizeSource(type,url);
  type=String(type).toLowerCase()==='browserfallback'&&source.type!=='GoogleDrive'?'BrowserFallback':source.type;
  url=source.url;
  const out=[];
  const l=type.toLowerCase();
  if(l==='googledrive')return collectDrive(source,tmp,{...options,probe});


  if(l==='directfile'){
    const dir=fs.mkdtempSync(path.join(tmp,'direct_'));
    const p=path.join(dir,'source'+(/\.(m3u8|mpd)(?:[?#]|$)/i.test(url)?'.mp4':'.bin'));
    await downloadBrowserCandidate({url},p,null,url);
    out.push({file:p,sourceUrl:url});
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
    const dir=fs.mkdtempSync(path.join(tmp,'youtube_'));
    fs.mkdirSync(dir,{recursive:true});
    cp.execFileSync('yt-dlp',['--no-playlist','-f','bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b','--merge-output-format','mp4','-o',path.join(dir,'source.%(ext)s'),url],{stdio:'inherit',timeout:180000,killSignal:'SIGKILL'});
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
          if(out.length>=(options.limit||1000)) break;
        }else fs.unlinkSync(p);
      }catch{if(fs.existsSync(p))fs.unlinkSync(p);}
    }
    return out;
  }

  if(l==='browserfallback'||l==='website'||l==='unknown'){
    const discovered=await genericBrowserDiscover(url,{maxPages:4,timeoutMs:45000,settleMs:4000});
    for(const c of discovered){
      const known=classifyBrowserUrl(c.url);
      let candidatePath;
      try{
        if(known!=='BrowserFallback'&&!isMediaUrl(c.url)){
          if(!known)continue;
          const nested=await collectSource(known,c.url,tmp,{...options,limit:Math.max(1,(options.limit||1000)-out.length)});
          for(const item of nested) out.push({...item,sourceUrl:item.sourceUrl||url,sourceType:item.sourceType||known,sourceAdapter:'ChromiumPlaywrightFallback'});
        }else{
          const p=path.join(tmp,'browser_fallback_'+Date.now()+'_'+out.length+(/\.(m3u8|mpd)(?:[?#]|$)/i.test(c.url)?'.mp4':'.bin'));
          candidatePath=p;
          await downloadBrowserCandidate(c,p,null,url);
          if(probe(p).valid)out.push({file:p,sourceUrl:url,discoveredMediaUrl:c.url,sourceAdapter:'ChromiumPlaywrightFallback'});
          else if(fs.existsSync(p))fs.unlinkSync(p);
        }
      }catch(e){
        console.warn('browser fallback candidate failed:',c.url,e.message);
        if(candidatePath&&fs.existsSync(candidatePath))fs.unlinkSync(candidatePath);
      }
      if(out.length>=(options.limit||1000))break;
    }
    return out;
  }

  return out;
}
async function collectWithFallback(source,tmp,options={}){
  source=normalizeSource(source.type,source.url);
  const collect=options.collect||collectSource;
  const diagnostics=options.diagnostics||[];
  let discovered=[];
  try{discovered=await collect(source.type,source.url,tmp,options);}
  catch(error){diagnostics.push({url:source.url,type:source.type,status:'SOURCE_FAILED',error:error.message});}
  if(!discovered.length&&!['GoogleDrive','BrowserFallback','Website'].includes(source.type)){
    try{discovered=await collect('BrowserFallback',source.url,tmp,options);}
    catch(error){diagnostics.push({url:source.url,type:source.type,status:'FALLBACK_FAILED',error:error.message});}
  }
  return discovered;
}

async function main(){
  const defaultType=String(process.env.INPUT_SOURCE_TYPE||'').trim();
  let rawSources;
  try{rawSources=JSON.parse(process.env.INPUT_SOURCE_URLS||'[]');}catch{throw new Error('SOURCE_URLS_INVALID_JSON');}
  const campaignId=String(process.env.INPUT_CAMPAIGN_ID||'').trim();
  const {sources,rejected}=normalizeSources(rawSources,defaultType,process.env.INPUT_SOURCE_URL);
  if(!campaignId||!sources.length)throw new Error('SOURCE_INPUT_INCOMPLETE: '+JSON.stringify(rejected));
  const targetAssets=Number(process.env.INPUT_MAX_ASSETS||2);
  if(!Number.isInteger(targetAssets)||targetAssets<1||targetAssets>1000)throw new Error('MAX_ASSETS_INVALID');
  const root=path.resolve(process.cwd());
  for(const name of ['phase4-input','.tmp-media']){
    const target=path.resolve(root,name);
    if(path.dirname(target)!==root)throw new Error('UNSAFE_OUTPUT_PATH');
    fs.rmSync(target,{recursive:true,force:true});
    fs.mkdirSync(target,{recursive:true});
  }
  const diagnostics=[];
  const verified=[];
  const seenDrive=new Set();
  const seenAssets=new Set();
  const deadline=Date.now()+18*60*1000;
  const timeLeft=()=>deadline-Date.now();
  let sourceCursor=0;
  const saveDiagnostics=()=>fs.writeFileSync('mediasilo-debug.json',JSON.stringify({
    campaignId,sourceUrls:sources,rejected,sourceDiagnostics:diagnostics,verifiedAssets:verified.length,
    createdAt:new Date().toISOString()
  },null,2));
  for(const source of sources){
    if(verified.length>=targetAssets||timeLeft()<=0)break;
    sourceCursor++;
    const discovered=await collectWithFallback(source,'.tmp-media',{
      limit:targetAssets-verified.length,seen:seenDrive,diagnostics,timeLeft
    });
    for(const candidate of discovered){
      const p=candidate.probe||probe(candidate.file);
      if(!p.valid)continue;
      const identity=candidate.sourceFileId?'GoogleDrive|'+candidate.sourceFileId:
        (candidate.sourceUrl||source.url)+'|'+(candidate.fileName||path.basename(candidate.file));
      if(seenAssets.has(identity))continue;
      seenAssets.add(identity);
      verified.push({...candidate,identity,sourceType:candidate.sourceType||source.type,probe:p});
      if(verified.length>=targetAssets)break;
    }
    saveDiagnostics();
  }
  if(!verified.length)throw new Error('SOURCE_MEDIA_INSUFFICIENT_VALID_VIDEO: see mediasilo-debug.json');
  const results=[];
  const crypto=require('crypto');
  for(const v of verified){
    if(timeLeft()<=0)throw new Error('ADAPTER_TIME_BUDGET_EXCEEDED');
    const assetId=v.sourceType.toLowerCase()+'-'+crypto.createHash('sha256').update(v.identity).digest('hex').slice(0,16);
    const fr=frames(v.file,path.join('phase4-input',assetId,'frames'),v.probe.duration);
    results.push({assetId,fileName:v.fileName||path.basename(v.file),durationSeconds:v.probe.duration,
      width:v.probe.width,height:v.probe.height,fileSizeBytes:v.probe.size,frameCount:fr.length,frames:fr,
      provenance:'normalized_source_real_media_ffprobe_extracted',sourceType:v.sourceType,
      sourceUrl:v.sourceUrl,sourceFileId:v.sourceFileId,parentSourceUrl:v.parentSourceUrl,
      relativePath:v.relativePath,sourceAdapter:v.sourceAdapter});
  }
  const manifest={complete:true,schemaVersion:'2.3',sourceType:sources[0].type,sourceUrls:sources,campaignId,
    videoAssetCount:results.length,frameCount:results.reduce((n,x)=>n+x.frameCount,0),results,
    fallbackUsed:results.some(x=>x.sourceAdapter==='ChromiumPlaywrightFallback'),
    selection:{maxAssets:targetAssets,sourcesAttempted:sourceCursor,totalSources:sources.length,
      unattemptedSources:sources.slice(sourceCursor),rejected,
      limited:sourceCursor<sources.length||diagnostics.some(d=>d.remainingMediaCount>0)},
    sourceDiagnostics:diagnostics,githubRunId:process.env.GITHUB_RUN_ID||null,
    githubRunAttempt:process.env.GITHUB_RUN_ATTEMPT||null,headSha:process.env.GITHUB_SHA||null,
    createdAt:new Date().toISOString()};
  fs.writeFileSync('phase4-input/phase4-input-manifest.json',JSON.stringify(manifest,null,2));
  saveDiagnostics();
  fs.writeFileSync('mediasilo-network.log','Normalized source worker completed.\n');
  console.log('PHASE4_ARTIFACT_VALIDATION_PASS');
}

if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={collectSource,collectWithFallback,main,probe,frames,classifyBrowserUrl};
