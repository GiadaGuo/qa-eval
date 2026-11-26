import type { SampleById } from '../api/datasetVersions'

export type Row = Record<string, unknown>
export const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(object) : []

function field(data: Row, tokens: unknown): unknown {
  if (!Array.isArray(tokens)) return undefined
  return tokens.reduce<unknown>((value, token) =>
    typeof token === 'string' && Object.hasOwn(object(value), token) ? object(value)[token] : undefined, data)
}

export function asText(text: string) {
  if (!/<[a-z!/][^>]*>/i.test(text)) return text
  const template = document.createElement('template')
  template.innerHTML = text
  template.content.querySelectorAll('script,style,template').forEach(node => node.remove())
  template.content.querySelectorAll('p,div,br,li,tr,h1,h2,h3,h4,section').forEach(node => node.after('\n'))
  return (template.content.textContent ?? '').trim()
}

export function sampleFields(record: SampleById, decision?: Row) {
  if ('contents' in record) return record.contents.flatMap(item => item.payload?.type === 'inline_text' && typeof item.payload.text === 'string'
    ? [{ id: item.content_id, label: item.role, text: item.payload.text, origin: undefined, rawKey: undefined }] : [])
  const data = record.data
  const origins = object(object(decision?.attributes).source_origins)
  const keys = new Set([...Object.keys(origins), 'title', 'body', 'html', 'content', 'text', '正文', '标题'])
  const seen = new Set<string>()
  return Array.from(keys).flatMap(id => {
    const origin = object(origins[id])
    const rawKey = Array.isArray(origin.field_tokens) && origin.field_tokens.length === 1 ? String(origin.field_tokens[0]) : id
    const text = field(data, origin.field_tokens) ?? data[id]
    if (typeof text !== 'string' || seen.has(rawKey)) return []
    seen.add(rawKey)
    return [{ id, label: id === 'title' || id === '标题' ? '标题' : id === 'plain' ? '纯文本正文' : '正文', text, origin, rawKey }]
  })
}

export function samplePreview(record: SampleById, decision: Row) {
  const fields = sampleFields(record, decision)
  const title = fields.find(item => item.label === '标题' || item.label.endsWith('.title'))
  const body = fields.find(item => item !== title)
  return { title: title && asText(title.text), summary: body && Array.from(asText(body.text).replace(/\s+/g, ' ')).slice(0, 180).join('') }
}

export async function verifiedExcerpt(record: SampleById, decision: Row, location: Row) {
  if (location.coordinate_space !== 'source_unicode_codepoint') return
  const item = sampleFields(record, decision).find(value => value.id === location.content_id)
  if (!item) return
  if ('data' in record) {
    const origin = item.origin
    if (!origin || origin.transform || origin.source_sha256 !== record.meta.artifact_sha256 ||
      origin.byte_start !== record.meta.byte_start || field(record.data, origin.field_tokens) !== item.text || !globalThis.crypto?.subtle) return
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(item.text))
    const sha = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
    if (origin.text_sha256 !== `sha256:${sha}`) return
  }
  const { start, end } = location
  if (typeof start !== 'number' || typeof end !== 'number' || !Number.isInteger(start) || !Number.isInteger(end)) return
  const chars = Array.from(item.text)
  if (start < 0 || end <= start || end > chars.length) return
  return { id: item.id, before: chars.slice(0, start).join(''), text: chars.slice(start, end).join(''), after: chars.slice(end).join('') }
}
