'use strict';
const fs=require('fs'),crypto=require('crypto');
const {normalizeSource}=require('./media-source-contract');
const {collectWithFallback,probe}=require('./universal-media-evidence-worker');
async function contentHash(file){const h=crypto.createHash('sha256');for await(const chunk of fs.createReadStream(file))h.update(chunk);return h.digest('hex');}
// Never assign new downloads to evidence IDs by directory or discovery order.
async function acquireMediaAssets(segments,tmp,options={}){
  const requests=new Map();
  for(const s of segments){
    const p=s.sourceMedia||{};
    if(!s.assetId||!p.sourceUrl||!p.sourceType||!/^[a-f0-9]{64}$/.test(p.contentSha256||''))throw new Error('PHASE7_MEDIA_PROVENANCE_MISSING: regenerate Phase 4A–6 evidence');
    if(requests.has(s.assetId)&&requests.get(s.assetId).sourceMedia.contentSha256!==p.contentSha256)throw new Error('PHASE7_MEDIA_PROVENANCE_CONFLICT');
    requests.set(s.assetId,s);
  }
  const found=new Map(),collected=new Map();
  const collect=options.collect||collectWithFallback,hash=options.hash||contentHash,validate=options.probe||probe;
  for(const [id,s] of requests){
    const p=s.sourceMedia;
    const source=normalizeSource(p.sourceType,p.sourceUrl),key=source.type+'|'+source.url;
    if(!collected.has(key))collected.set(key,await collect(source,tmp,{...options,limit:Math.max(1,requests.size)}));
    for(const item of collected.get(key)){
      if(await hash(item.file)!==p.contentSha256)continue;
      const info=item.probe||validate(item.file);if(!info.valid)continue;
      found.set(id,{...item,assetId:id,fileName:s.fileName,sourceType:source.type,sourceUrl:source.url,contentSha256:p.contentSha256,probe:info});break;
    }
    if(!found.has(id))throw new Error('PHASE7_EXACT_MEDIA_UNAVAILABLE: '+id);
  }
  return [...requests.keys()].map(id=>found.get(id));
}
module.exports={acquireMediaAssets,contentHash};
