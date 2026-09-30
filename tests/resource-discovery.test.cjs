'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { validateGraph } = require('../.github/scripts/validate-resource-discovery.js');
const root = path.resolve(__dirname, '..');

// Unit fixtures only; these are not campaign/runtime evidence.
function fixture() {
  return { schemaVersion: '1.0', campaignId: 'unit-only', status: 'NO_MEDIA_SOURCE_DISCOVERED',
    resources: [], resourceCount: 0, mediaCandidates: [], browserFallbackCandidates: [],
    primaryMediaSources: [], failures: [], createdAt: '2026-09-30T00:00:00Z' };
}

test('actual discovery worker and validator compile before dispatch', () => {
  for (const name of ['resource-discovery-worker.js', 'validate-resource-discovery.js']) {
    cp.execFileSync(process.execPath, ['--check', path.join(root, '.github/scripts', name)]);
  }
});

test('no-media is a valid diagnostic result, not successful media proof', () => {
  const graph = fixture();
  assert.equal(validateGraph(graph, 'unit-only').status, 'NO_MEDIA_SOURCE_DISCOVERED');
});

test('rejects wrong campaign and false claims of discovered media', () => {
  assert.throws(() => validateGraph(fixture(), 'another-campaign'), /identity mismatch/);
  assert.throws(() => validateGraph({ ...fixture(), status: 'MEDIA_SOURCE_DISCOVERED' }, 'unit-only'),
    /without candidates/);
  assert.throws(() => validateGraph({ ...fixture(), status: 'BROWSER_FALLBACK_AVAILABLE' }, 'unit-only'),
    /without candidates/);
  assert.throws(() => validateGraph({ ...fixture(), resourceCount: 1 }, 'unit-only'), /count mismatch/);
});

test('discovery pipeline propagates node failure through tee', () => {
  const bash = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/bash';
  assert.ok(fs.existsSync(bash), 'bash is required to verify the Actions pipeline failure contract');
  const result = cp.spawnSync(bash, ['-c', 'set -euo pipefail; (exit 23) 2>&1 | tee /dev/null'], { encoding: 'utf8' });
  assert.equal(result.status, 23, result.stderr);
  const workflow = fs.readFileSync(path.join(root, '.github/workflows/universal-resource-discovery.yml'), 'utf8');
  assert.match(workflow, /shell: bash\s+run: \|\s+set -euo pipefail/);
  assert.match(workflow, /run: node \.github\/scripts\/validate-resource-discovery\.js/);
});
