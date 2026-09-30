'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { normalizeSource, normalizeSources } = require('../.github/scripts/media-source-contract');
const { collectDrive, mediaEntries } = require('../.github/scripts/google-drive-media');
const { collectWithFallback, classifyBrowserUrl } = require('../.github/scripts/universal-media-evidence-worker');

// Synthetic unit fixtures, never real campaign or media evidence.
const id = 'unit_fixture_file_00000000001';
const id2 = 'unit_fixture_file_00000000002';
const folder = 'unit_fixture_folder_00000001';
const fileUrl = value => `https://drive.google.com/file/d/${value}/view`;
const folderUrl = `https://drive.google.com/drive/folders/${folder}`;
const entry = (value, name) => ({ url: `https://drive.google.com/uc?id=${value}`, path: name });
const validProbe = () => ({ valid: true, duration: 12, width: 1920, height: 1080, size: 1000 });

for (const url of [fileUrl(id), `https://drive.google.com/open?id=${id}`, `https://drive.google.com/uc?export=download&id=${id}`,
  `https://drive.google.com/file/u/0/d/${id}/preview`, `https://drive.usercontent.google.com/download?id=${id}`]) {
  test(`normalizes Drive file form ${url}`, () => {
    assert.deepEqual(normalizeSource('GoogleDriveFile', url), { type: 'GoogleDrive', url: fileUrl(id), sourceKind: 'file', sourceId: id });
  });
}
test('detects folder from URL even when the supplied type says file', () => {
  assert.equal(normalizeSource('GoogleDriveFile', `https://drive.google.com/drive/u/0/folders/${folder}`).sourceKind, 'folder');
});
test('Drive host always wins over incorrect fallback/YouTube labels', () => {
  for (const type of ['YouTube', 'BrowserFallback', 'website', 'unknown']) assert.equal(normalizeSource(type, fileUrl(id)).type, 'GoogleDrive');
});
test('rejects invalid URLs and spoofed Drive URLs', () => {
  for (const url of ['javascript:alert(1)', 'file:///tmp/a', 'https://drive.google.com.evil.test/file/d/' + id + '/view',
    'https://example.com/?next=' + fileUrl(id), 'https://drive.google.com/drive/my-drive',
    'https://drive.google.com/open?id=a&id=b', 'https://user:password@drive.google.com/file/d/' + id + '/view']) {
    assert.throws(() => normalizeSource('GoogleDrive', url));
  }
});
test('deduplicates aliases while preserving resource keys and unrelated folders', () => {
  const result = normalizeSources([{type:'GoogleDriveFile',url:fileUrl(id)}, {type:'GoogleDrive',url:`https://drive.google.com/uc?id=${id}&resourcekey=unit-key`},
    {type:'GoogleDrive',url:folderUrl}, {type:'DirectFile',url:'javascript:nope'}], '', '');
  assert.equal(result.sources.length, 2);
  assert.match(result.sources[0].url, /resourcekey=unit-key/);
  assert.equal(result.sources[1].sourceKind, 'folder');
  assert.equal(result.rejected.length, 1);
});
test('single-source handoff works when optional source_urls is empty', () => {
  assert.equal(normalizeSources([], 'GoogleDriveFile', fileUrl(id)).sources.length, 1);
  assert.throws(() => normalizeSources({}, '', ''), /MUST_BE_ARRAY/);
});
test('preserves non-Drive source classification', () => {
  for (const [url,type] of [['https://app.mediasilo.com/review/unit','MediaSilo'],['https://example.com/video.mp4','DirectFile'],
    ['https://example.com/live.m3u8','DirectFile'],['https://youtu.be/unit','YouTube'],['https://www.dropbox.com/s/unit','Dropbox'],
    ['https://we.tl/unit','WeTransfer'],['https://next.frame.io/unit','NextFrame']]) assert.equal(classifyBrowserUrl(url),type);
});
test('folder entries filter non-media, invalid hosts, duplicates and resolve ambiguous names', () => {
  const result = mediaEntries([entry(id,'nested/clip.MP4'), entry(id,'duplicate.mp4'), entry(id2,'Video without extension'),
    entry('image_fixture_0000000001','logo.png'), {url:'https://youtu.be/unit',path:'shortcut.mp4'}, entry('doc_fixture_00000000001','brief.pdf')]);
  assert.equal(result.length,2);
  assert.equal(result[0].fileName,'clip.MP4');
  assert.equal(result[1].needsMetadata,true);
});

function fixture(t, listing, behaviour = {}) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'adapter-unit-'));
  t.after(() => fs.rmSync(tmp, {recursive:true,force:true}));
  const calls=[];
  const diagnostics=[];
  const run=(command,args,options)=>{
    calls.push({command,args,options});
    assert.equal(command,'gdown');
    assert.ok(options.timeout > 0 && options.timeout <= 150000);
    if(args.includes('--json')) {
      if(behaviour.listError)throw behaviour.listError;
      if(args[0]===folderUrl)return JSON.stringify(listing);
      return JSON.stringify(behaviour.metadata || listing);
    }
    if(behaviour.downloadError)throw behaviour.downloadError;
    fs.writeFileSync(args[args.indexOf('-O')+1], 'unit-fixture-only');
    return '';
  };
  return {tmp,calls,diagnostics,run,options:{run,diagnostics,probe:behaviour.probe||validProbe,limit:behaviour.limit||1,seen:new Set()}};
}
test('bounded folder downloads keep exact file identity and original filename', t => {
  const f=fixture(t,[entry(id,'nested/clip.mp4'),entry(id2,'another.mp4')]);
  const result=collectDrive({type:'GoogleDrive',url:folderUrl},f.tmp,f.options);
  assert.equal(result.length,1);
  assert.equal(result[0].sourceUrl,fileUrl(id));
  assert.equal(result[0].parentSourceUrl,folderUrl);
  assert.equal(result[0].fileName,'clip.mp4');
  assert.equal(f.calls.filter(c=>c.args.includes('-O')).length,1);
  assert.equal(f.diagnostics[0].remainingMediaCount,1);
});
test('extensionless folder video resolves through file metadata before bytes are downloaded', t => {
  const f=fixture(t,[entry(id,'Interview')],{metadata:[entry(id,'Interview.mp4')]});
  const result=collectDrive({type:'GoogleDrive',url:folderUrl},f.tmp,f.options);
  assert.equal(result[0].fileName,'Interview.mp4');
  assert.equal(f.calls.length,3);
});
test('empty folders and documents stop without downloading', t => {
  for(const [listing,status] of [[[],'EMPTY_FOLDER'],[[entry(id,'brief.pdf')],'NO_MEDIA_FILES']]) {
    const f=fixture(t,listing);
    assert.deepEqual(collectDrive({type:'GoogleDrive',url:folderUrl},f.tmp,f.options),[]);
    assert.equal(f.diagnostics[0].status,status);
    assert.equal(f.calls.length,1);
  }
});
test('file and folder duplicate IDs download only once', t => {
  const f=fixture(t,[entry(id,'clip.mp4')]);
  collectDrive({type:'GoogleDriveFile',url:fileUrl(id)},f.tmp,f.options);
  collectDrive({type:'GoogleDrive',url:folderUrl},f.tmp,f.options);
  assert.equal(f.calls.filter(c=>c.args.includes('-O')).length,1);
});
test('download timeouts and invalid media return diagnostics, never candidates', t => {
  for(const behaviour of [{downloadError:Object.assign(new Error('timeout'),{code:'ETIMEDOUT'})},{probe:()=>({valid:false})}]) {
    const f=fixture(t,[entry(id,'clip.mp4')],behaviour);
    assert.deepEqual(collectDrive({type:'GoogleDrive',url:fileUrl(id)},f.tmp,f.options),[]);
    assert.equal(f.diagnostics[0].status,'GOOGLE_DRIVE_MEDIA_UNAVAILABLE');
    assert.ok(f.diagnostics[0].failed.length);
  }
});
test('Drive empty and failed results NEVER invoke browser fallback, including mislabeled URLs', async () => {
  for(const type of ['GoogleDrive','GoogleDriveFile','YouTube','BrowserFallback']) {
    for(const fail of [false,true]) {
      const calls=[];
      const result=await collectWithFallback({type,url:fileUrl(id)},'.',{collect:async t=>{calls.push(t);if(fail)throw Error('quota');return [];}});
      assert.deepEqual(result,[]);
      assert.deepEqual(calls,['GoogleDrive']);
    }
  }
});
test('non-Drive adapter failures retain their existing fallback path', async () => {
  const calls=[];
  await collectWithFallback({type:'MediaSilo',url:'https://app.mediasilo.com/review/unit'},'.',{
    collect:async type=>{calls.push(type);return [];}
  });
  assert.deepEqual(calls,['MediaSilo','BrowserFallback']);
});
