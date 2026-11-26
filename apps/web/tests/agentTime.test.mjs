import assert from 'node:assert/strict'
import test from 'node:test'
import { formatAgentRelativeTime, formatAgentTime } from '../src/utils/agentTime.ts'

test('Agent timestamps display in Beijing time with or without an explicit offset', () => {
  assert.equal(formatAgentTime('2026-11-26T07:17:58.123456'), '2026/11/26 15:17:58')
  assert.equal(formatAgentTime('2026-11-26T07:17:58Z', 'time'), '15:17:58')
  assert.equal(formatAgentTime('2026-11-26T15:17:58+08:00', 'time'), '15:17:58')
})

test('Agent history computes elapsed time and day boundaries in Beijing time', () => {
  const now = new Date('2026-11-26T16:30:00Z')
  assert.equal(formatAgentRelativeTime('2026-11-26T16:29:55', now), '刚刚')
  assert.equal(formatAgentRelativeTime('2026-11-26T16:25:00', now), '5分钟前')
  assert.equal(formatAgentRelativeTime('2026-11-26T15:00:00', now), '2026/11/26')
})
