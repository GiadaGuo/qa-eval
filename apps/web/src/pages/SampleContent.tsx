import { Collapse, Empty, Space, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { SampleById } from '../api/datasetVersions'

import { asText, sampleFields, verifiedExcerpt, type Row } from './samplePresentation'

export function SampleContent({ record, decision, location }: { record: SampleById; decision?: Row; location?: Row }) {
  const fields = sampleFields(record, decision)
  const [position, setPosition] = useState<Awaited<ReturnType<typeof verifiedExcerpt>>>()
  useEffect(() => {
    let active = true
    if (decision && location) void verifiedExcerpt(record, decision, location).then(value => { if (active) setPosition(value) })
    return () => { active = false }
  }, [record, decision, location])
  const other = 'data' in record ? Object.fromEntries(Object.entries(record.data).filter(([key]) => !fields.some(field => field.rawKey === key))) : record.metadata
  return <Space orientation="vertical" style={{ width: '100%' }}>
    {!fields.length && <Empty description="该样本没有可展示的标题或正文" />}
    {fields.map(item => <section key={item.id}>
      <Typography.Text strong>{item.label}</Typography.Text>
      <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {position?.id === item.id ? <>{position.before}<mark ref={node => node?.scrollIntoView({ block: 'center' })}>{position.text}</mark>{position.after}</> : asText(item.text)}
      </div>
    </section>)}
    <Collapse items={[{ key: 'raw', label: '其他原始字段', children: <pre className="evidence-row-json">{JSON.stringify(other, null, 2)}</pre> }]} />
  </Space>
}
