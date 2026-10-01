'use strict';

const { normalizeSources } = require('./media-source-contract');
const { collectDrive, driveAssetId } = require('./google-drive-media');

// Reacquire only the exact Drive identities verified in Phase 4A.
// Older order-based IDs must regenerate evidence rather than guess footage.
function acquireDriveAssets(assetSource, requiredAssetIds, tmp, options = {}) {
  if (!requiredAssetIds.length || requiredAssetIds.some(id => !/^googledrive-[a-f0-9]{16}$/.test(id))) {
    throw new Error('PHASE7_DRIVE_ASSET_PROVENANCE_MISSING: regenerate Phase 4A evidence');
  }
  const raw = Array.isArray(assetSource.sourceUrls) ? assetSource.sourceUrls : [];
  const { sources } = normalizeSources(raw, assetSource.sourceType,
    assetSource.sourceUrl || assetSource.officialContentFolderUrl);
  const wantedAssetIds = new Set(requiredAssetIds);
  const found = new Map();
  const seen = new Set();
  const collect = options.collect || collectDrive;
  for (const source of sources) {
    if (source.type !== 'GoogleDrive') continue;
    const remaining = new Set([...wantedAssetIds].filter(id => !found.has(id)));
    if (!remaining.size) break;
    for (const item of collect(source, tmp, {...options, seen, wantedAssetIds:remaining, limit:remaining.size})) {
      const id = driveAssetId(item.sourceFileId);
      if (!wantedAssetIds.has(id)) throw new Error('PHASE7_DRIVE_UNREQUESTED_ASSET');
      found.set(id, {...item, assetId:id});
    }
  }
  const missing = requiredAssetIds.filter(id => !found.has(id));
  if (missing.length) throw new Error('PHASE7_DRIVE_EXACT_ASSETS_UNAVAILABLE: ' + missing.join(','));
  return requiredAssetIds.map(id => found.get(id));
}

module.exports = { acquireDriveAssets };
