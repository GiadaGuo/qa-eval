import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compareMetricVersions,
  latestCalculatorBindings,
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

test('Registry keeps the highest calculator version per metric binding', () => {
  const previous = { metric_key: '0201', metric_version: '1.0.0', calculator_key: 'policy_document_format', calculator_version: '1.0.0', enabled: true }
  const current = { ...previous, calculator_version: '1.1.0' }
  const otherCalculator = { ...previous, calculator_key: 'format_compliance' }
  const otherMetricVersion = { ...previous, metric_version: '2.0.0' }
  assert.deepEqual(
    latestCalculatorBindings([previous, otherCalculator, current, otherMetricVersion, previous]),
    [current, otherCalculator, otherMetricVersion],
  )
  const disabledUpdate = { ...current, calculator_version: '1.10.0', enabled: false }
  assert.deepEqual(latestCalculatorBindings([disabledUpdate, current]), [disabledUpdate])
})
