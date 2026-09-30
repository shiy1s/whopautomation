'use strict';

const fs = require('node:fs');
const assert = require('node:assert/strict');

function validateGraph(graph, campaignId) {
  assert.ok(campaignId, 'CAMPAIGN_ID is required');
  assert.equal(graph.campaignId, campaignId, 'Discovery campaign identity mismatch');
  assert.equal(graph.schemaVersion, '1.0', 'Unsupported discovery schema');
  assert.ok(['MEDIA_SOURCE_DISCOVERED', 'BROWSER_FALLBACK_AVAILABLE',
    'NO_MEDIA_SOURCE_DISCOVERED'].includes(graph.status), 'Invalid discovery status');
  for (const key of ['resources', 'mediaCandidates', 'browserFallbackCandidates', 'primaryMediaSources', 'failures']) {
    assert.ok(Array.isArray(graph[key]), `Discovery ${key} must be an array`);
  }
  assert.equal(graph.resourceCount, graph.resources.length, 'Discovery resource count mismatch');
  assert.ok(Number.isFinite(Date.parse(graph.createdAt)), 'Discovery creation time missing');
  if (graph.status === 'MEDIA_SOURCE_DISCOVERED') {
    assert.ok(graph.mediaCandidates.length > 0 && graph.primaryMediaSources.length > 0,
      'Discovery claims media without candidates');
  }
  if (graph.status === 'BROWSER_FALLBACK_AVAILABLE') {
    assert.ok(graph.browserFallbackCandidates.length > 0 && graph.primaryMediaSources.length > 0,
      'Discovery claims fallback without candidates');
  }
  return graph;
}

if (require.main === module) {
  const graph = JSON.parse(fs.readFileSync('resource-discovery-artifact/resource-graph.json', 'utf8'));
  validateGraph(graph, process.env.CAMPAIGN_ID);
  console.log('RESOURCE_DISCOVERY_ARTIFACT_VALIDATION_PASS');
}

module.exports = { validateGraph };
