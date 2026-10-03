const test=require('node:test'),assert=require('node:assert/strict');
const {acquireMediaAssets}=require('../.github/scripts/phase7-media-sources');
const {normalizeSource}=require('../.github/scripts/media-source-contract');
const hashA='a'.repeat(64),hashB='b'.repeat(64);
const seg=(id,hash,type,url)=>({assetId:id,fileName:id+'.mp4',sourceMedia:{sourceType:type,sourceUrl:url,contentSha256:hash}});
// Mocked downloads represent control flow only, never real campaign media.
for(const [type,url] of [['MediaSilo','https://app.mediasilo.com/review/unit'],['DirectFile','https://unit.example/video.mp4'],['YouTube','https://youtu.be/unit'],['Dropbox','https://dropbox.com/s/unit'],['BrowserFallback','https://unit.example/page']]){
test(type+' reacquisition follows verified hashes despite reordered downloads',async()=>{
 const result=await acquireMediaAssets([seg('a',hashA,type,url),seg('b',hashB,type,url)],'unused',{
 collect:async()=>[{file:'b'},{file:'a'}],hash:async f=>f==='a'?hashA:hashB,probe:()=>({valid:true})});
 assert.deepEqual(result.map(x=>x.file),['a','b']);assert.deepEqual(result.map(x=>x.assetId),['a','b']);
});}
test('changed footage and missing provenance fail instead of substituting',async()=>{
 await assert.rejects(acquireMediaAssets([seg('a',hashA,'DirectFile','https://unit.example/video.mp4')],'unused',{collect:async()=>[{file:'wrong'}],hash:async()=>hashB}),/EXACT_MEDIA_UNAVAILABLE/);
 await assert.rejects(acquireMediaAssets([{assetId:'old'}],'unused',{collect:async()=>assert.fail('must not download')}),/PROVENANCE_MISSING/);
});
test('Drive trailing DNS dot is rejected before browser fallback',()=>assert.throws(()=>normalizeSource('BrowserFallback','https://drive.google.com./file/d/unit_fixture_00000001/view'),/SOURCE_URL_INVALID/));
