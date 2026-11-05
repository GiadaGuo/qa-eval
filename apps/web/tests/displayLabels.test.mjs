import assert from 'node:assert/strict'
import test from 'node:test'

import {
  datasetVersionStatusLabel,
  formatModalityList,
  metricResultStatusLabel,
  modalityLabel,
  passFailStatusLabel,
  presenceLabel,
  presencePolicyLabel,
  scoringEligibilityLabel,
  severityLabel,
} from '../src/utils/displayLabels.ts'

test('dataset version statuses render as Chinese labels', () => {
  assert.equal(datasetVersionStatusLabel('DRAFT'), '草稿')
  assert.equal(datasetVersionStatusLabel('PUBLISHED'), '已发布')
  assert.equal(datasetVersionStatusLabel('RETIRED'), '已退役')
})

test('modalities translate for display while raw values stay available', () => {
  assert.equal(modalityLabel('text'), '文本')
  assert.equal(modalityLabel('structured'), '结构化')
  assert.equal(formatModalityList(['text', 'image']), '文本、图像')
})

test('presence enums only translate display labels', () => {
  assert.equal(presenceLabel('PRESENT'), '存在')
  assert.equal(presenceLabel('NULL'), '空值')
  assert.equal(presenceLabel('MISSING'), '缺失')
  assert.equal(presencePolicyLabel('REJECT_MISSING'), '缺失即拒绝')
})

test('gate / rule / metric statuses translate', () => {
  assert.equal(passFailStatusLabel('PASS'), '通过')
  assert.equal(passFailStatusLabel('FAIL'), '未通过')
  assert.equal(metricResultStatusLabel('OK'), '正常')
  assert.equal(metricResultStatusLabel('PARTIAL'), '部分覆盖')
})

test('severity and scoring eligibility translate', () => {
  assert.equal(severityLabel('CRITICAL'), '严重')
  assert.equal(scoringEligibilityLabel('SCOREABLE'), '可评分')
  assert.equal(scoringEligibilityLabel('OBSERVATION_ONLY'), '仅观察')
})

test('unknown enum values fall back to the raw wire value', () => {
  assert.equal(datasetVersionStatusLabel('SOMETHING_NEW'), 'SOMETHING_NEW')
  assert.equal(passFailStatusLabel(null), '—')
})
