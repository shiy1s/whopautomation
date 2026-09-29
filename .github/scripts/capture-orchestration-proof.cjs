'use strict';
const fs = require('node:fs');
const intent = process.env.ORCHESTRATION_INTENT;
if (!/^[a-zA-Z0-9_-]{1,140}$/.test(intent || '')) throw new Error('Invalid dispatch intent');
const phase = process.env.PHASE;
const read = path => JSON.parse(fs.readFileSync(path, 'utf8'));
const proof = { schemaVersion: 1, intent, phase, campaignId: process.env.CAMPAIGN_ID,
  runId: Number(process.env.GITHUB_RUN_ID), runAttempt: Number(process.env.GITHUB_RUN_ATTEMPT), headSha: process.env.GITHUB_SHA };
if (phase === 'discovery') {
  const g = read('resource-discovery-artifact/resource-graph.json');
  if (g.campaignId !== proof.campaignId) throw new Error('Campaign mismatch');
  proof.graph = { campaignId:g.campaignId,status:g.status,mediaCandidates:g.mediaCandidates.map(x=>({type:x.type,url:x.url})) };
} else if (phase === '4A') {
  const m = read('phase4-input/phase4-input-manifest.json');
  if (m.campaignId !== proof.campaignId || m.complete !== true || m.videoAssetCount < 1 || m.frameCount < 1) throw new Error('Invalid media manifest');
  proof.media = {campaignId:m.campaignId,complete:m.complete,videoAssetCount:m.videoAssetCount,frameCount:m.frameCount,
    results:m.results.map(a=>({assetId:a.assetId,fileName:a.fileName,durationSeconds:a.durationSeconds,width:a.width,height:a.height,
      fileSizeBytes:a.fileSizeBytes,frameCount:a.frameCount,frames:a.frames,provenance:a.provenance}))};
} else throw new Error('Unsupported pilot phase');
const output = JSON.stringify(proof);
if (Buffer.byteLength(output) > 800000) throw new Error('Proof too large for checkpoint');
fs.writeFileSync('orchestration-proof.json',output);
