'use strict';
// Keep the reviewed standalone Code-node patch aligned with the worker contract.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const contract = fs.readFileSync(path.join(root, '.github/scripts/media-source-contract.js'), 'utf8')
  .replace(/^'use strict';\s*/, '').replace(/\nmodule\.exports = [\s\S]*$/, '').trim();
const target = path.join(root, 'docs/n8n/universal-adapter-operations.json');
const patch = JSON.parse(fs.readFileSync(target, 'utf8'));
for (const op of patch.operations) {
  const code = op.parameters.jsCode;
  const start = code.indexOf('// Also embedded unchanged');
  if (start < 0) continue;
  const end = code.indexOf('\n}', code.indexOf('function normalizeSources')) + 2;
  if (end < 2) throw new Error('Contract boundary missing');
  op.parameters.jsCode = code.slice(0, start) + contract + code.slice(end);
}
fs.writeFileSync(target, JSON.stringify(patch, null, 2) + '\n');
