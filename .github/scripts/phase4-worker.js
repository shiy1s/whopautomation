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
    const isFolder=/drive\.google\.com\/drive\/folders\//i.test(sourceUrl);
    try {
      if (isFolder) {
        cp.execFileSync('gdown',['--folder','--continue','--retries','3',sourceUrl,'-O',tmpMediaDir],{stdio:'inherit'});
      } else {
        const singleName='drive_source.mp4';
        cp.execFileSync('gdown',['--continue','--retries','3',sourceUrl,'-O',path.join(tmpMediaDir,singleName)],{stdio:'inherit'});
      }
    } catch(e) {
      throw new Error('GOOGLE_DRIVE_DOWNLOAD_FAILED: '+(e.stderr?String(e.stderr):e.message));
    }
    const walkFiles=(dir)=>{
      const out=[];
      for(const ent of fs.readdirSync(dir,{withFileTypes:true})){
        const p=path.join(dir,ent.name);
        if(ent.isDirectory()) out.push(...walkFiles(p));
        else out.push(p);
      }
      return out;
    };
    const videoFiles=walkFiles(tmpMediaDir).filter(p=>/\.(mp4|mov|m4v|webm|mkv)$/i.test(p));
    if(videoFiles.length<2) throw new Error('GOOGLE_DRIVE_SOURCE_INSUFFICIENT_VIDEO: found '+videoFiles.length+' downloadable video files; at least 2 are required by the current render contract.');
    const selected=videoFiles.sort((a,b)=>a.localeCompare(b)).slice(0,2);
    for(const filePath of selected){
      const probe=runFFprobe(filePath);
      if(!probe.valid||probe.duration<10) { phase4Diagnostics.failures.push(path.relative(tmpMediaDir,filePath)+': ffprobe failed or duration below 10s'); continue; }
      const hash=require('crypto').createHash('sha256').update(sourceUrl+'|'+path.relative(tmpMediaDir,filePath)).digest('hex').slice(0,24);
      verifiedSources.push({assetId:'gdrive-'+hash,fileName:path.basename(filePath),folder:path.dirname(path.relative(tmpMediaDir,filePath))||'root',filePath,sourceUrl});
    }
    if(verifiedSources.length!==2) throw new Error('GOOGLE_DRIVE_SOURCE_ACCEPTANCE_FAILED: fewer than 2 verified playable videos.');
  } else {

