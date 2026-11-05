import assert from 'node:assert/strict'
import test from 'node:test'

import {
  mappingToEditorState,
  rulesForUnmodifiedField,
  selectInitialMapping,
} from '../src/pages/mappingEditorState.ts'

const fields = [
  { name: 'iid', data_type: 'string', nullable: false, is_primary_key: false },
  { name: 'content', data_type: 'string', nullable: false, is_primary_key: false },
  { name: 'pubtime', data_type: 'string', nullable: true, is_primary_key: false },
]

const recommended = {
  schema_version: '2.0',
  adapter: { key: 'jhsjk_policy_article', version: '2.0.0' },
  mappings: [
    { source_path: '$.iid', action: 'MAP', target: { kind: 'SAMPLE_ID', name: 'sample_id' }, presence_policy: 'REJECT_MISSING', required_for_normalization: true },
    { source_path: '$.content', action: 'MAP', target: { kind: 'CONTENT', name: 'body-html', role: 'article.body.html', modality: 'text' }, presence_policy: 'PRESERVE', required_for_normalization: false },
    { source_path: '$.content', action: 'MAP', target: { kind: 'CONTENT', name: 'body-plain', role: 'article.body.plain', modality: 'text' }, presence_policy: 'PRESERVE', required_for_normalization: false },
    { source_path: '$.pubtime', action: 'MAP', target: { kind: 'TEMPORAL', name: 'published_at' }, presence_policy: 'PRESERVE', required_for_normalization: false },
  ],
}

test('recommended mapping initializes visible UI groups', () => {
  const state = mappingToEditorState(fields, recommended)
  assert.equal(state.assignments.iid, 'sample_id')
  assert.equal(state.assignments.content, 'content_fields')
  assert.equal(state.contentSettings.content.role, 'article.body.html')
  assert.equal(state.assignments.pubtime, 'time_fields')
  assert.equal(state.timeSettings.pubtime.targetName, 'published_at')
})

test('unmodified content preserves one-to-many template rules', () => {
  const rules = rulesForUnmodifiedField(recommended, 'content', new Set())
  assert.deepEqual(rules.map(rule => rule.target.role), [
    'article.body.html',
    'article.body.plain',
  ])
  assert.equal(rulesForUnmodifiedField(recommended, 'content', new Set(['content'])).length, 0)
})

test('saved mapping takes priority over recommendation on refresh or version switch', () => {
  const saved = structuredClone(recommended)
  saved.mappings[1].target.role = 'article.body.custom'
  assert.equal(selectInitialMapping(saved, recommended), saved)
  assert.equal(selectInitialMapping(null, recommended), recommended)
})
