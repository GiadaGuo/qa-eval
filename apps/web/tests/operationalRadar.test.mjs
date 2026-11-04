import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRadarSegments } from '../src/utils/operationalRadar.ts'

test('missing radar axes break lines instead of filling or interpolating them', () => {
  const segments = buildRadarSegments(
    [100, 90, null, 80, null, 70, 60, 50],
    (index, score) => ({ x: index, y: score }),
  )
  assert.deepEqual(segments, ['0,100 1,90', '5,70 6,60 7,50'])
})

test('isolated radar points are not converted into fabricated line segments', () => {
  assert.deepEqual(buildRadarSegments([null, 75, null], (index, score) => ({ x: index, y: score })), [])
})
