import assert from 'node:assert/strict'
import test from 'node:test'

import { codePointRangeToUtf16, splitHighlight } from '../src/utils/textHighlight.ts'

test('converts Unicode code point offsets across surrogate pairs', () => {
  const text = '甲😀敏感乙'
  const range = codePointRangeToUtf16(text, 2, 4)
  assert.deepEqual(range, { start: 3, end: 5 })
  assert.deepEqual(splitHighlight(text, range), { before: '甲😀', highlighted: '敏感', after: '乙' })
})

test('rejects invalid ranges', () => {
  assert.equal(codePointRangeToUtf16('abc', 2, 2), null)
  assert.equal(codePointRangeToUtf16('abc', 0, 4), null)
})
