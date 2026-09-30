'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const patch=require('../docs/n8n/universal-adapter-operations.json');
const code=patch.operations.find(o=>o.nodeName==='Prepare Phase 4A Runner').parameters.jsCode;
const vm=require('node:vm');
// Model the real n8n Cloud sandbox: no URL constructor or require().
const run=input=>vm.runInNewContext('(function(){'+code+'})()',{$input:{first:()=>({json:input})}})[0].json;
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

const runnerCode=require('../docs/n8n/phase-runner-diagnostics-operations.json').operations[0].parameters.jsCode;
test('Phase 4A failures retain the actual conclusion and cannot claim unavailable media',()=>{
 const base={phaseName:'Phase 4A Media Evidence Extraction',phaseRun:{id:42}};
 const run=r=>vm.runInNewContext('(function(){'+runnerCode+'})()',{$input:{first:()=>({json:r})},$:()=>({first:()=>({json:base})})})[0].json;
 for(const conclusion of ['failure','cancelled','timed_out']){
  const result=run({id:42,status:'completed',conclusion});
  assert.equal(result.phase4aSourceCheck.status,'BLOCKED');
  assert.equal(result.phase4aSourceCheck.reason,'GITHUB_PHASE_RUN_'+conclusion.toUpperCase());
  assert.equal(result.phase4aSourceCheck.retryable,false);
  assert.equal(result.githubPhase.conclusion,conclusion);
 }
 assert.equal(run({id:42,status:'completed',conclusion:'success'}).phase4aSourceCheck,undefined);
 assert.throws(()=>run({id:43,status:'completed',conclusion:'success'}),/RUN_ID_MISMATCH/);
});

test('rule validation accepts the same Drive hosts as the worker without lowering evidence gates',()=>{
 const validation=patch.operations.find(o=>o.nodeName==='Validate Campaign Rules').parameters.jsCode;
 const input={campaignBrief:{parsedRules:{campaignId:'unit-only'},ruleEvidence:Array.from({length:7},()=>({evidence:'unit'}))},
  campaignBriefLinks:{sourceType:'GoogleDrive',sourceUrl:'https://drive.usercontent.google.com/download?id=unit_fixture_0000000001'}};
 const run=()=>vm.runInNewContext('(function(){'+validation+'})()',{$input:{first:()=>({json:input})}})[0].json;
 assert.equal(run().campaignRulesValidation.status,'valid');
 input.campaignBrief.ruleEvidence=[];
 assert.throws(run,/evidence set incomplete/);
});
