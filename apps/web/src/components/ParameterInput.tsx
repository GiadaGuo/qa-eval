import { Input, InputNumber, Select, Switch } from 'antd'

export default function ParameterInput({ schema, value, onChange }: { schema: Record<string, unknown>; value: unknown; onChange: (value: unknown) => void }) {
  const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== 'null') : schema.type
  if (Array.isArray(schema.enum)) return <Select style={{ width: '100%' }} value={value} options={schema.enum.map((item) => ({ label: String(item), value: item }))} onChange={onChange} />
  if (type === 'boolean') return <Switch checked={Boolean(value)} onChange={onChange} />
  if (type === 'number' || type === 'integer') return <InputNumber style={{ width: '100%' }} value={typeof value === 'number' ? value : null} min={schema.minimum as number | undefined} max={schema.maximum as number | undefined} precision={type === 'integer' ? 0 : undefined} onChange={onChange} />
  if (type === 'object' || type === 'array') return <Input.TextArea value={JSON.stringify(value ?? (type === 'array' ? [] : {}), null, 2)} autoSize={{ minRows: 2, maxRows: 8 }} onChange={(event) => { try { onChange(JSON.parse(event.target.value)) } catch { /* keep editing until valid JSON */ } }} />
  return <Input value={value == null ? '' : String(value)} onChange={(event) => onChange(event.target.value)} />
}

