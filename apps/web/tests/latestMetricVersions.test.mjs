import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compareMetricVersions,
  latestMetricVersions,
} from '../src/utils/latestMetricVersions.ts'

test('keeps only the latest version for each metric key', () => {
  const metrics = [
    { metric_key: '0204', version: '1.0.0', name: 'old' },
    { metric_key: '0208', version: '1.0.0', name: 'cleanliness' },
    { metric_key: '0204', version: '2.0.0', name: 'latest' },
  ]

  assert.deepEqual(latestMetricVersions(metrics), [
    { metric_key: '0204', version: '2.0.0', name: 'latest' },
    { metric_key: '0208', version: '1.0.0', name: 'cleanliness' },
  ])
})

test('compares numeric semantic versions instead of lexicographic strings', () => {
  assert.ok(compareMetricVersions('1.10.0', '1.9.0') > 0)
  assert.ok(compareMetricVersions('2.0.0', '2.0.0-rc.1') > 0)
  assert.equal(compareMetricVersions('v2.0', '2.0.0'), 0)
})
