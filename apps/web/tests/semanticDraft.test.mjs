import { test } from 'node:test'
import assert from 'node:assert/strict'
import { addRecommendedBindings } from '../src/utils/semanticDraft.ts'

const item = (semantic, representation, field, confirmed) => ({
  binding: { semantic, representation, field_tokens: [field] },
  confirmed,
})

test('recommendations preserve saved bindings and add only pending candidates', () => {
  const saved = [item('document.title', 'text/plain', 'manual_title', true)]
  const source = [
    item('document.title', 'text/plain', 'suggested_title', true),
    item('document.body.plain', 'text/plain', 'content', true),
    item('document.body.plain', 'text/plain', 'alternative', false),
  ]
  assert.deepEqual(addRecommendedBindings(saved, source), [
    item('document.body.plain', 'text/plain', 'content', false),
  ])
  assert.equal(saved[0].confirmed, true)
  assert.equal(source[1].confirmed, true)
})

test('one raw field may provide different semantic representations', () => {
  const html = item('document.body.html', 'text/html', 'content', true)
  const plain = item('document.body.plain', 'text/plain', 'content', false)
  assert.deepEqual(addRecommendedBindings([html], [html, plain]), [plain])
})
