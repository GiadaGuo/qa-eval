import { useState } from 'react'
import { Input, InputNumber, Select, Switch } from 'antd'

export default function ParameterInput({ id, schema, value, onChange }: { id?: string; schema: Record<string, unknown>; value?: unknown; onChange?: (value: unknown) => void }) {
  const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== 'null') : schema.type
  if (Array.isArray(schema.enum)) return <Select id={id} style={{ width: '100%' }} value={value} options={schema.enum.map((item) => ({ label: String(item), value: item }))} onChange={onChange} />
  if (type === 'boolean') return <Switch id={id} checked={Boolean(value)} onChange={onChange} />
  if (type === 'number' || type === 'integer') return <InputNumber id={id} style={{ width: '100%' }} value={typeof value === 'number' ? value : null} min={schema.minimum as number | undefined} max={schema.maximum as number | undefined} precision={type === 'integer' ? 0 : undefined} onChange={onChange} />
  if (type === 'object' || type === 'array') return <JsonParameter id={id} value={value} array={type === 'array'} onChange={onChange} />
  return <Input id={id} value={value == null ? '' : String(value)} onChange={(event) => onChange?.(event.target.value)} />
}



function JsonParameter({ id, value, array, onChange }: { id?: string; value: unknown; array: boolean; onChange?: (value: unknown) => void }) {
  const [draft, setDraft] = useState<{ value: unknown; text: string }>()
  return <Input.TextArea id={id} value={draft?.value === value && draft ? draft.text : JSON.stringify(value ?? (array ? [] : {}), null, 2)}
    autoSize={{ minRows: 2, maxRows: 8 }} onChange={event => {
      const text = event.target.value
      try {
        const parsed: unknown = JSON.parse(text)
        if (array ? !Array.isArray(parsed) : !parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setDraft({ value, text })
          return
        }
        setDraft({ value: parsed, text })
        onChange?.(parsed)
      } catch {
        setDraft({ value, text })
      }
    }} />
}
