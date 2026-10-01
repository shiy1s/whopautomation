const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../docs/n8n/campaign-brief-parser.js'),'utf8');
test('brief parser retains raw restrictions, exact display text and global attribution',()=>{
 const raw='YOUTUBE SHORTS and INSTAGRAM\nTag @unit in the caption on every post.\nShow the whole home with on-screen text that says “UNIT”.\nDo not add other brand watermarks.\nNo paying for boosts\nDo not post raw rips';
 const source={campaignPackage:{campaign:{id:'unit',name:'Unit',referenceMaterials:['https://example.com/brief']}}};
 const out=vm.runInNewContext('(function(){'+code+'})()',{$input:{all:()=>[{json:{data:raw,referenceType:'Text',referenceUrl:'https://example.com/brief'}}]},$:()=>({first:()=>({json:source})})})[0].json;
 const r=out.campaignBrief.parsedRules;
 assert.ok(r.rawText.includes('No paying for boosts'));
 assert.equal(r.onScreenText.requiredLines.join(','),'UNIT');
 assert.equal(r.platforms.youtubeShorts.accountTag,'@unit');
 assert.equal(r.platforms.instagram.accountTag,'@unit');
 assert.equal(r.publishing.paidBoostingForbidden,true);
});
