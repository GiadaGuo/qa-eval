export interface TextRange { start: number; end: number }

/** Convert API Unicode-code-point offsets to JavaScript UTF-16 string offsets. */
export function codePointRangeToUtf16(text: string, start: number, end: number): TextRange | null {
  const points = Array.from(text)
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > points.length) return null
  const before = points.slice(0, start).join('').length
  return { start: before, end: before + points.slice(start, end).join('').length }
}

export function splitHighlight(text: string, range: TextRange | null) {
  if (!range) return { before: text, highlighted: '', after: '' }
  return {
    before: text.slice(0, range.start),
    highlighted: text.slice(range.start, range.end),
    after: text.slice(range.end),
  }
}
