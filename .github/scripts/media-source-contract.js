'use strict';

// Also embedded unchanged in the n8n Phase 4A preparation node.
// n8n Cloud Code runners expose neither URL nor the Node url module.
// Parse the HTTP subset we accept without depending on sandbox globals.
function parseSourceUrl(raw) {
  const value = String(raw || '').trim();
  const match = value.match(/^(https?):\/\/([^/?#]+)([^?#]*)(\?[^#]*)?(?:#.*)?$/i);
  if (!match || /[\s\\\u0000-\u001f\u007f]/.test(value) || /%(?![0-9a-f]{2})/i.test(value)) {
    throw new Error('SOURCE_URL_INVALID');
  }
  const authority = match[2].toLowerCase();
  const hostPort = authority.match(/^(\[[0-9a-f:]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::(\d{1,5}))?$/i);
  if (!hostPort || (hostPort[2] && Number(hostPort[2]) > 65535)) throw new Error('SOURCE_URL_INVALID');
  const protocol = match[1].toLowerCase();
  const hostname = hostPort[1];
  const port = hostPort[2] && !((protocol === 'https' && Number(hostPort[2]) === 443) ||
    (protocol === 'http' && Number(hostPort[2]) === 80)) ? ':' + hostPort[2] : '';
  const pathname = match[3] || '/';
  const query = match[4] || '';
  const params = [];
  try {
    for (const pair of query.slice(1).split('&').filter(Boolean)) {
      const separator = pair.indexOf('=');
      const key = separator < 0 ? pair : pair.slice(0, separator);
      const val = separator < 0 ? '' : pair.slice(separator + 1);
      params.push([decodeURIComponent(key.replace(/\+/g, ' ')), decodeURIComponent(val.replace(/\+/g, ' '))]);
    }
  } catch { throw new Error('SOURCE_URL_INVALID'); }
  return { hostname, pathname, port, href: protocol + '://' + hostname + port + pathname + query,
    searchParams: { get: key => params.find(p => p[0] === key)?.[1] || null,
      getAll: key => params.filter(p => p[0] === key).map(p => p[1]) } };
}

function normalizeSource(type, rawUrl) {
  const names = ['MediaSilo', 'GoogleDrive', 'GoogleDriveFile', 'Dropbox', 'WeTransfer',
    'DirectFile', 'YouTube', 'NextFrame', 'Website', 'BrowserFallback'];
  const requested = names.find(n => n.toLowerCase() === String(type || '').trim().toLowerCase());
  const url = parseSourceUrl(rawUrl);
  const host = url.hostname.toLowerCase();
  const driveHost = ['drive.google.com', 'drive.usercontent.google.com'].includes(host);
  if (driveHost || requested === 'GoogleDrive' || requested === 'GoogleDriveFile') {
    if (!driveHost || url.port) throw new Error('GOOGLE_DRIVE_HOST_INVALID');
    const folder = url.pathname.match(/^\/(?:drive\/(?:u\/\d+\/)?)?folders\/([A-Za-z0-9_-]+)\/?$/);
    const file = url.pathname.match(/^\/file\/(?:u\/\d+\/)?d\/([A-Za-z0-9_-]+)(?:\/(?:view|preview|edit))?\/?$/);
    const queryFile = ['/open', '/uc', '/download'].includes(url.pathname) && url.searchParams.getAll('id').length === 1
      ? url.searchParams.get('id') : null;
    const id = folder?.[1] || file?.[1] || queryFile;
    if (!id || !/^[A-Za-z0-9_-]{10,}$/.test(id)) throw new Error('GOOGLE_DRIVE_ID_INVALID');
    const kind = folder ? 'folder' : 'file';
    let canonical = kind === 'folder'
      ? 'https://drive.google.com/drive/folders/' + id
      : 'https://drive.google.com/file/d/' + id + '/view';
    const resourceKey = url.searchParams.get('resourcekey');
    if (resourceKey) canonical += '?resourcekey=' + encodeURIComponent(resourceKey);
    return { type: 'GoogleDrive', url: canonical, sourceKind: kind, sourceId: id };
  }
  const hostIs = domain => host === domain || host.endsWith('.' + domain);
  let inferred;
  if (hostIs('youtube.com') || host === 'youtu.be') inferred = 'YouTube';
  else if (hostIs('dropbox.com')) inferred = 'Dropbox';
  else if (hostIs('mediasilo.com')) inferred = 'MediaSilo';
  else if (hostIs('frame.io')) inferred = 'NextFrame';
  else if (hostIs('wetransfer.com') || host === 'we.tl') inferred = 'WeTransfer';
  else if (/\.(mp4|mov|m4v|webm|mkv|avi|mpeg|mpg|ts|m3u8|mpd)$/i.test(url.pathname)) inferred = 'DirectFile';
  const normalized = inferred || requested || 'BrowserFallback';
  if (normalized === 'YouTube' && inferred !== 'YouTube') throw new Error('YOUTUBE_HOST_INVALID');
  return { type: normalized, url: url.href };
}

function normalizeSources(rawSources, defaultType, primaryUrl) {
  if (!Array.isArray(rawSources)) throw new Error('SOURCE_URLS_MUST_BE_ARRAY');
  const raw = rawSources.length ? rawSources : (primaryUrl ? [{ type: defaultType, url: primaryUrl }] : []);
  const byKey = new Map();
  const rejected = [];
  for (const entry of raw) {
    try {
      const s = normalizeSource(typeof entry === 'object' && entry ? entry.type || defaultType : defaultType,
        typeof entry === 'object' && entry ? entry.url : entry);
      const key = s.sourceId ? 'GoogleDrive|' + s.sourceKind + '|' + s.sourceId : s.type + '|' + s.url;
      const existing = byKey.get(key);
      if (!existing || (!existing.url.includes('resourcekey=') && s.url.includes('resourcekey='))) byKey.set(key, s);
    } catch (error) { rejected.push({ url: String(entry?.url || entry || ''), reason: error.message }); }
  }
  const sources = [...byKey.values()].sort((a, b) =>
    Number(b.sourceKind === 'file') - Number(a.sourceKind === 'file'));
  return { sources, rejected };
}

module.exports = { normalizeSource, normalizeSources };
