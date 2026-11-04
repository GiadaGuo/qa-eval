import assert from 'node:assert/strict'
import test from 'node:test'

import {
  evaluationRunStatusLabel,
  isEvaluationRunTerminal,
  shouldAutoPollEvaluationRun,
} from '../src/utils/evaluationRunStatus.ts'

test('all canonical terminal statuses stop polling', () => {
  for (const status of ['PARTIAL_SUCCEEDED', 'SUCCEEDED', 'FAILED', 'CANCELED']) {
    assert.equal(isEvaluationRunTerminal(status), true)
  }
  for (const status of ['PENDING', 'RUNNING', 'CANCEL_REQUESTED']) {
    assert.equal(isEvaluationRunTerminal(status), false)
  }
})

test('failed status is never presented as running', () => {
  assert.equal(evaluationRunStatusLabel('FAILED'), '评测失败')
  assert.equal(evaluationRunStatusLabel('RUNNING'), '运行中')
})

test('only owned non-terminal runs are automatically polled', () => {
  const running = { status: 'RUNNING', execution: { worker_id: 'worker-1' } }
  assert.equal(shouldAutoPollEvaluationRun(running), true)
  assert.equal(shouldAutoPollEvaluationRun({ ...running, execution: {} }), false)
  assert.equal(shouldAutoPollEvaluationRun({ ...running, status: 'SUCCEEDED' }), false)
})
