'use strict';

const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const { normalizeSource } = require('./media-source-contract');
const VIDEO_NAME = /\.(mp4|mov|m4v|webm|mkv|avi|mpeg|mpg|ts)$/i;
const NON_MEDIA_NAME = /\.(pdf|txt|md|docx?|xlsx?|pptx?|csv|json|url|lnk|jpg|jpeg|png|gif|webp|svg|zip|rar|7z|mp3|wav|m4a)$/i;

function mediaEntries(listing) {
  if (!Array.isArray(listing)) throw new Error('GOOGLE_DRIVE_LISTING_INVALID');
  const seen = new Set();
  const entries = [];
  for (const entry of listing) {
    if (!entry || typeof entry.path !== 'string' || NON_MEDIA_NAME.test(entry.path)) continue;
    let source;
    try { source = normalizeSource('GoogleDrive', entry.url); } catch { continue; }
    if (source.sourceKind !== 'file' || seen.has(source.sourceId)) continue;
    seen.add(source.sourceId);
    entries.push({ ...source, fileName: path.posix.basename(entry.path.replace(/\\/g, '/')),
      relativePath: entry.path, needsMetadata: !VIDEO_NAME.test(entry.path) });
  }
  return entries;
}

function failureReason(error) {
  if (error.code === 'ETIMEDOUT' || error.killed || /timed?\s*out/i.test(error.message)) return 'GOOGLE_DRIVE_TIMEOUT';
  if (/quota|too many|download limit/i.test(String(error.stderr || '') + error.message)) return 'GOOGLE_DRIVE_QUOTA';
  return 'GOOGLE_DRIVE_ACCESS_OR_DOWNLOAD_FAILED';
}

function collectDrive(source, tmp, options = {}) {
  const run = options.run || cp.execFileSync;
  const probe = options.probe;
  const diagnostics = options.diagnostics || [];
  const seen = options.seen || new Set();
  const limit = options.limit ?? 2;
  const timeLeft = options.timeLeft || (() => 150000);
  const s = normalizeSource(source.type, source.url);
  const report = { type: 'GoogleDrive', url: s.url, sourceKind: s.sourceKind, sourceId: s.sourceId,
    status: 'LISTING', downloaded: 0, failed: [], skippedNonMedia: 0 };
  diagnostics.push(report);
  const bound = maximum => {
    const left = timeLeft();
    if (left <= 0) throw Object.assign(new Error('ADAPTER_TIME_BUDGET_EXCEEDED'), { code: 'ETIMEDOUT' });
    return Math.max(1, Math.min(maximum, left));
  };
  if (s.sourceKind === 'file' && seen.has(s.sourceId)) { report.status = 'DUPLICATE'; return []; }
  let listing;
  try {
    const args = [s.url, '--json', '--quiet', '--no-cookies', '--timeout', '20'];
    if (s.sourceKind === 'folder') args.push('--folder');
    listing = JSON.parse(run('gdown', args, { encoding: 'utf8', timeout: bound(45000),
      killSignal: 'SIGKILL', maxBuffer: 8 * 1024 * 1024 }));
    if (!Array.isArray(listing)) throw new Error('GOOGLE_DRIVE_LISTING_INVALID');
  } catch (error) {
    report.status = failureReason(error);
    report.error = String(error.stderr || error.message).slice(0, 1200);
    return [];
  }
  const entries = mediaEntries(listing);
  report.listed = listing.length;
  report.mediaCount = entries.length;
  report.skippedNonMedia = listing.length - entries.length;
  if (!listing.length) { report.status = 'EMPTY_FOLDER'; return []; }
  if (!entries.length) { report.status = 'NO_MEDIA_FILES'; return []; }
  if (s.sourceKind === 'file' && (entries.length !== 1 || entries[0].sourceId !== s.sourceId)) {
    report.status = 'GOOGLE_DRIVE_LISTING_ID_MISMATCH'; return [];
  }
  const out = [];
  let skippedDuplicates = 0;
  for (const entry of entries) {
    if (out.length >= limit) break;
    if (seen.has(entry.sourceId)) { skippedDuplicates++; continue; }
    seen.add(entry.sourceId);
    if (entry.needsMetadata) {
      try {
        const metadata = JSON.parse(run('gdown', [entry.url, '--json', '--quiet', '--no-cookies', '--timeout', '20'],
          { encoding: 'utf8', timeout: bound(30000), killSignal: 'SIGKILL', maxBuffer: 1024 * 1024 }));
        const resolved = mediaEntries(metadata).filter(e => !e.needsMetadata && e.sourceId === entry.sourceId);
        if (resolved.length !== 1) { report.skippedNonMedia++; continue; }
        entry.fileName = resolved[0].fileName;
      } catch (error) {
        report.failed.push({ sourceId: entry.sourceId, reason: failureReason(error) });
        continue;
      }
    }
    const dir = path.join(tmp, 'gdrive_' + entry.sourceId);
    fs.mkdirSync(dir, { recursive: true });
    const safeName = entry.fileName.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
    const target = path.join(dir, safeName);
    try {
      // A single-file resource key belongs to that file, never to every folder child.
      const fileUrl = s.sourceKind === 'file' ? s.url : entry.url;
      run('gdown', ['--no-cookies', '--retries', '1', '--timeout', '30', fileUrl, '-O', target],
        { encoding: 'utf8', timeout: bound(150000), killSignal: 'SIGKILL', maxBuffer: 2 * 1024 * 1024 });
      const metadata = fs.existsSync(target) ? probe(target) : { valid: false };
      if (!metadata.valid) throw new Error('GOOGLE_DRIVE_INVALID_VIDEO');
      out.push({ file: target, fileName: entry.fileName, sourceType: 'GoogleDrive',
        sourceUrl: fileUrl, sourceFileId: entry.sourceId, parentSourceUrl: s.url,
        relativePath: entry.relativePath, probe: metadata });
      report.downloaded++;
    } catch (error) {
      report.failed.push({ sourceId: entry.sourceId,
        reason: error.message === 'GOOGLE_DRIVE_INVALID_VIDEO' ? error.message : failureReason(error) });
      if (fs.existsSync(target)) fs.unlinkSync(target);
    }
  }
  report.remainingMediaCount = entries.filter(e => !seen.has(e.sourceId)).length;
  report.skippedDuplicates = skippedDuplicates;
  report.status = out.length ? (report.failed.length ? 'PARTIAL' : 'READY') :
    (report.failed.length ? 'GOOGLE_DRIVE_MEDIA_UNAVAILABLE' :
      (skippedDuplicates === entries.length ? 'DUPLICATE' : 'NO_MEDIA_FILES'));
  return out;
}

module.exports = { collectDrive, mediaEntries, failureReason };
