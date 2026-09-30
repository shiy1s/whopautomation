'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const patch=require('../docs/n8n/universal-adapter-operations.json');
const code=patch.operations.find(o=>o.nodeName==='Prepare Phase 4A Runner').parameters.jsCode;
const run=input=>new Function('$input',code)({first:()=>({json:input})})[0].json;
const file='https://drive.google.com/file/d/unit_fixture_0000000001/view';
const folder='https://drive.google.com/drive/folders/unit_folder_0000000001';
test('n8n handoff preserves Drive file/folder meaning and deduplicates aliases',()=>{
 const input={campaignId:'unit-only',executionMode:'manual',campaignBriefLinks:{resourceGraph:{campaignId:'unit-only',primaryMediaSources:[
  {type:'GoogleDriveFile',url:file},{type:'GoogleDrive',url:file+'?usp=sharing'},{type:'GoogleDrive',url:folder}
 ]}}};
 const output=run(input),sources=JSON.parse(output.inputs.source_urls);
 assert.equal(output.inputs.source_type,'GoogleDrive');
 assert.equal(output.inputs.source_url,file);
 assert.deepEqual(sources.map(s=>s.sourceKind),['file','folder']);
 assert.equal(output.executionMode,'manual');
 assert.equal(output.workflowFile,'universal-media-evidence-v2.yml');
});
test('wrong campaign graph and entirely invalid sources stop before dispatch',()=>{
 assert.throws(()=>run({campaignId:'unit-only',campaignBriefLinks:{resourceGraph:{campaignId:'wrong',primaryMediaSources:[]}}}),/CAMPAIGN_MISMATCH/);
 assert.throws(()=>run({campaignId:'unit-only',campaignBriefLinks:{resourceGraph:{primaryMediaSources:[{type:'GoogleDrive',url:'javascript:nope'}]}}}),/CANDIDATES_MISSING/);
});
test('all changed Code nodes compile without executing their external workflow',()=>{
 for(const op of patch.operations)new Function('$input','$',op.parameters.jsCode);
});
