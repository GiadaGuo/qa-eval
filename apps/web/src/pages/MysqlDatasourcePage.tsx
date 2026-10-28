/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-22 02:58:25
 * @LastEditTime: 2026-10-22 09:50:45
 * @Description: 
 */

import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Input, Popover, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import {
  inspectUploadedSource,
  saveFieldMapping,
  type FieldMappingSpec,
  type SourcePreviewRow,
  type SourcePreviewValue,
  type UploadedSourceInspection,
} from '../api/datasetVersions'

const { Title, Text } = Typography
const PREVIEW_TEXT_LENGTH = 300

type MappingGroup =
  | 'unassigned'
  | 'sample_id'
  | 'content_fields'
  | 'annotation_fields'
  | 'metadata_fields'
  | 'time_fields'
  | 'source_fields'
  | 'passthrough'
  | 'ignored'
type ContentModality = 'text' | 'image' | 'audio' | 'video' | 'structured'
type TextFormat = 'plain' | 'html' | 'markdown' | 'code'
type Assignments = Record<string, MappingGroup>
type ContentSettings = Record<string, {
  modality: ContentModality
  role: string
  format: TextFormat
  requiredForNormalization: boolean
}>
type TimeSettings = Record<string, {
  targetName: string
  sourceTimezone: string
}>
const GROUPS: { value: MappingGroup; label: string; description: string }[] = [
  { value: 'unassigned', label: '未配置', description: '尚未决定用途，确认映射前必须处理' },
  { value: 'sample_id', label: 'sample_id', description: '唯一标识，必须恰好一个' },
  { value: 'content_fields', label: '内容字段', description: '可评测的文本、图像、音频、视频或结构化内容' },
  { value: 'annotation_fields', label: '标注字段', description: '标签、偏好、区间或其他监督信号' },
  { value: 'metadata_fields', label: '元数据', description: '描述样本但不直接作为主要内容的属性' },
  { value: 'time_fields', label: '时间字段', description: '需要解析、命名和确认时区的业务时间' },
  { value: 'source_fields', label: '来源属性', description: 'URL、来源名称等可追溯信息' },
  { value: 'passthrough', label: '透传字段', description: '保留原值，暂不赋予正式 canonical 语义' },
  { value: 'ignored', label: '忽略字段', description: '用户明确选择不进入标准化记录' },
]
const SUGGESTED: Assignments = {
  id: 'sample_id',
  content: 'content_fields', plain_text_content: 'content_fields',
  title: 'content_fields', summary: 'content_fields',
  article_id: 'metadata_fields',
  url: 'source_fields', origin_name: 'source_fields',
  pub_date: 'time_fields', create_time: 'time_fields',
}
const SUGGESTED_CONTENT: ContentSettings = {
  content: { modality: 'text', role: 'article.body.html', format: 'html', requiredForNormalization: false },
  plain_text_content: { modality: 'text', role: 'article.body.plain', format: 'plain', requiredForNormalization: false },
  title: { modality: 'text', role: 'article.title', format: 'plain', requiredForNormalization: false },
  summary: { modality: 'text', role: 'article.summary', format: 'plain', requiredForNormalization: false },
}
const SUGGESTED_TIME: TimeSettings = {
  pub_date: { targetName: 'published_at', sourceTimezone: 'unknown' },
  create_time: { targetName: 'source_created_at', sourceTimezone: 'unknown' },
}
function defaultAssignments(schema: UploadedSourceInspection): Assignments {
  const knownTable = schema.fields.some(field => field.name === 'plain_text_content')
  return Object.fromEntries(schema.fields.map(f => [
    f.name, knownTable ? (SUGGESTED[f.name] ?? 'unassigned') : 'unassigned',
  ]))
}
function defaultContentSetting(name: string) {
  return SUGGESTED_CONTENT[name] ?? {
    modality: 'text' as const,
    role: '',
    format: 'plain' as const,
    requiredForNormalization: false,
  }
}
function defaultTimeSetting(name: string) {
  return SUGGESTED_TIME[name] ?? { targetName: name, sourceTimezone: 'unknown' }
}
function displayValue(value: SourcePreviewValue | undefined) {
  if (value == null) return <Text type="secondary">NULL</Text>
  if (value === '') return <Text type="secondary">空字符串</Text>
  return <Text style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{String(value)}</Text>
}
function FieldSample({ name, rows }: { name: string; rows: SourcePreviewRow[] }) {
  return (
    <Popover trigger="hover" placement="right" title={`字段样例：${name}`} content={
      <div style={{ width: 320, maxWidth: '70vw' }}>
        {rows.length === 0 ? <Text type="secondary">暂无样例数据</Text> :
          rows.slice(0, 3).map((row, index) => (
            <div key={index} style={{ marginBottom: 10 }}>
              <Text type="secondary">样例 {index + 1}</Text>
              <div style={{ maxHeight: 100, overflowY: 'auto' }}>{displayValue(row[name])}</div>
            </div>
          ))}
      </div>
    }>
      <Text code style={{ cursor: 'help', overflowWrap: 'anywhere' }}>{name}</Text>
    </Popover>
  )
}

export default function MysqlDatasourcePage({ versionId, onBack }: { versionId: string; onBack?: () => void }) {
  const [schema, setSchema] = useState<UploadedSourceInspection | null>(null)
  const [rows, setRows] = useState<SourcePreviewRow[]>([])
  const [limit, setLimit] = useState(5)
  const [expanded, setExpanded] = useState(false)
  const [schemaLoading, setSchemaLoading] = useState(true)
  const [previewLoading, setPreviewLoading] = useState(true)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [assignments, setAssignments] = useState<Assignments>({})
  const [contentSettings, setContentSettings] = useState<ContentSettings>(SUGGESTED_CONTENT)
  const [timeSettings, setTimeSettings] = useState<TimeSettings>(SUGGESTED_TIME)
  const [selected, setSelected] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const loadSchema = useCallback(async () => {
    setSchemaLoading(true)
    setSchemaError(null)
    try {
      const data = await inspectUploadedSource(versionId, limit, PREVIEW_TEXT_LENGTH)
      setSchema(data)
      setRows(data.rows)
      setAssignments(previous => {
        const defaults = defaultAssignments(data)
        return Object.fromEntries(data.fields.map(f => [f.name, previous[f.name] ?? defaults[f.name]]))
      })
      setSelected(previous => data.fields.some(f => f.name === previous) ? previous : (data.fields[0]?.name ?? null))
    } catch (error) {
      setSchemaError(error instanceof Error ? error.message : '未知错误')
    } finally {
      setSchemaLoading(false)
    }
  }, [limit, versionId])

  const loadPreview = useCallback(async (nextLimit: number) => {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const data = await inspectUploadedSource(versionId, nextLimit, PREVIEW_TEXT_LENGTH)
      setSchema(data)
      setRows(data.rows)
    } catch (error) {
      setRows([])
      setPreviewError(error instanceof Error ? error.message : '未知错误')
    } finally {
      setPreviewLoading(false)
    }
  }, [versionId])

  useEffect(() => {
    let cancelled = false
    inspectUploadedSource(versionId, 5, PREVIEW_TEXT_LENGTH).then(data => {
      if (cancelled) return
      setSchema(data)
      setRows(data.rows)
      setAssignments(defaultAssignments(data))
      setSelected(data.fields[0]?.name ?? null)
    }).catch((error: unknown) => {
      if (!cancelled) setSchemaError(error instanceof Error ? error.message : '未知错误')
    }).finally(() => {
      if (!cancelled) {
        setSchemaLoading(false)
        setPreviewLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [versionId])

  const fields = schema?.fields ?? []
  const currentField = fields.find(f => f.name === selected)
  const grouped = (group: MappingGroup) => fields.filter(f => (assignments[f.name] ?? 'unassigned') === group)
  const unassignedCount = grouped('unassigned').length
  const contentConfigurationValid = grouped('content_fields').every(field => {
    const setting = contentSettings[field.name] ?? defaultContentSetting(field.name)
    return setting.role.trim().length > 0
  })
  const timeConfigurationValid = grouped('time_fields').every(field => {
    const setting = timeSettings[field.name] ?? defaultTimeSetting(field.name)
    return setting.targetName.trim().length > 0
  })
  const valid = grouped('sample_id').length === 1
    && grouped('content_fields').length >= 1
    && unassignedCount === 0
    && contentConfigurationValid
    && timeConfigurationValid

  function assign(name: string, group: MappingGroup) {
    setAssignments(previous => {
      const next = { ...previous }
      if (group === 'sample_id') {
        for (const field of Object.keys(next)) {
          if (next[field] === 'sample_id') next[field] = 'unassigned'
        }
      }
      next[name] = group
      return next
    })
    if (group === 'content_fields') {
      setContentSettings(previous => ({
        ...previous,
        [name]: previous[name] ?? defaultContentSetting(name),
      }))
    }
    if (group === 'time_fields') {
      setTimeSettings(previous => ({
        ...previous,
        [name]: previous[name] ?? defaultTimeSetting(name),
      }))
    }
  }

  const selectedGroup = selected ? (assignments[selected] ?? 'unassigned') : null
  const selectedContent = selected ? (contentSettings[selected] ?? defaultContentSetting(selected)) : null
  const selectedTime = selected ? (timeSettings[selected] ?? defaultTimeSetting(selected)) : null

  const previewColumns: TableColumnsType<SourcePreviewRow> = fields.map(f => ({
    title: f.name, dataIndex: f.name, key: f.name, width: 220,
    render: (value: SourcePreviewValue | undefined) =>
      <div style={{ maxHeight: 100, overflowY: 'auto', overflowWrap: 'anywhere' }}>{displayValue(value)}</div>,
  }))

  async function persistMapping() {
    if (!valid) return
    const mappings: FieldMappingSpec['mappings'] = fields.map(field => {
      const group = assignments[field.name] ?? 'unassigned'
      const base = { source_path: `$.${field.name}`, presence_policy: 'PRESERVE' as const, required_for_normalization: false, value_type: field.data_type }
      if (group === 'ignored') return { ...base, action: 'IGNORE' as const }
      if (group === 'passthrough') return { ...base, action: 'PASSTHROUGH' as const }
      if (group === 'sample_id') return { ...base, action: 'MAP' as const, target: { kind: 'SAMPLE_ID' }, presence_policy: 'REJECT_MISSING' as const, required_for_normalization: true }
      if (group === 'content_fields') { const setting = contentSettings[field.name] ?? defaultContentSetting(field.name); return { ...base, action: 'MAP' as const, target: { kind: 'CONTENT', role: setting.role, modality: setting.modality }, required_for_normalization: setting.requiredForNormalization } }
      if (group === 'metadata_fields') return { ...base, action: 'MAP' as const, target: { kind: 'METADATA', name: field.name } }
      if (group === 'source_fields') return { ...base, action: 'MAP' as const, target: { kind: 'SOURCE_ATTRIBUTE', name: field.name } }
      if (group === 'time_fields') return { ...base, action: 'MAP' as const, target: { kind: 'TEMPORAL', name: (timeSettings[field.name] ?? defaultTimeSetting(field.name)).targetName } }
      if (group === 'annotation_fields') return { ...base, action: 'MAP' as const, target: { kind: 'ANNOTATION', name: field.name } }
      throw new Error(`字段 ${field.name} 尚未配置`)
    })
    setSaving(true)
    try { await saveFieldMapping(versionId, { schema_version: '2.0', adapter: { key: 'jsonl_mapping', version: '2.0.0' }, mappings }); onBack?.() } finally { setSaving(false) }
  }

  return (
    <div style={{ width: '100%', maxWidth: 1440, minWidth: 0, margin: '0 auto', padding: 24, boxSizing: 'border-box', overflowX: 'clip' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, minWidth: 0 }}>
        <div>
          {onBack && <Button onClick={onBack} style={{ marginBottom: 12 }}>返回数据集</Button>}
          <Title level={2}>已上传 JSONL 字段映射</Title>
          <Text type="secondary">字段结构与样例来自当前 Dataset Version 的固定上传产物，不依赖实时 MySQL。</Text>
        </div>
        {schemaError && <Alert type="error" showIcon title="Schema 读取失败" description={schemaError} />}
        {previewError && <Alert type="warning" showIcon title="样例读取失败" description={previewError} />}
        <Card title="数据源信息" extra={<Button loading={schemaLoading || previewLoading} onClick={() => { void loadSchema(); void loadPreview(limit) }}>重新加载</Button>}>
          <Descriptions bordered column={{ xs: 1, sm: 2, md: 3 }}>
            <Descriptions.Item label="来源">上传的 JSONL</Descriptions.Item>
            <Descriptions.Item label="文件">{schema?.filename ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="记录数">{schema?.record_count ?? '—'}</Descriptions.Item>
          </Descriptions>
        </Card>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16, minWidth: 0, alignItems: 'start' }}>
          <Card title={`左侧 · 数据源结构（${fields.length}）`} loading={schemaLoading} style={{ minWidth: 0 }}>
            <Text type="secondary">点击选中字段，悬停字段名查看前 3 条样例。</Text>
            <div style={{ marginTop: 12, maxHeight: 560, overflowY: 'auto' }}>
              {fields.map(f => (
                <div key={f.name} onClick={() => setSelected(f.name)} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: 10, marginBottom: 8, border: selected === f.name ? '2px solid var(--forest-500)' : '1px solid var(--border)', borderRadius: 8, cursor: 'pointer', minWidth: 0, background: selected === f.name ? 'var(--sage-100)' : 'var(--surface)' }}>
                  <div style={{ minWidth: 0 }}>
                    <FieldSample name={f.name} rows={rows} />
                    <div><Text type="secondary" style={{ fontSize: 12 }}>{f.data_type}{f.is_primary_key ? ' · PK' : ''}{f.nullable ? ' · NULL' : ' · NOT NULL'}</Text></div>
                  </div>
                  <Tag color={(assignments[f.name] ?? 'unassigned') === 'unassigned' ? 'warning' : undefined} style={{ marginRight: 0, whiteSpace: 'normal' }}>{GROUPS.find(g => g.value === (assignments[f.name] ?? 'unassigned'))?.label}</Tag>
                </div>
              ))}
            </div>
          </Card>
          <Card title="右侧 · Canonical 映射配置" style={{ minWidth: 0 }}>
            {currentField ? (
              <div style={{ marginBottom: 16 }}>
                <Text type="secondary">当前选择：</Text> <Text strong>{currentField.name}</Text>
                <div style={{ marginTop: 8 }}>
                  <Select<MappingGroup> aria-label="映射目标" style={{ width: '100%' }} value={assignments[currentField.name] ?? 'unassigned'} options={GROUPS.map(g => ({ value: g.value, label: g.label }))} onChange={group => assign(currentField.name, group)} />
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>“未配置”不会静默透传；更换 sample_id 会把原字段恢复为未配置。</Text>

                {selectedGroup === 'content_fields' && selected && selectedContent && (
                  <Card size="small" title="内容语义" style={{ marginTop: 12 }}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <div>
                        <Text type="secondary">模态</Text>
                        <Select<ContentModality>
                          aria-label="内容模态"
                          style={{ width: '100%', marginTop: 4 }}
                          value={selectedContent.modality}
                          options={['text', 'image', 'audio', 'video', 'structured'].map(value => ({ value, label: value }))}
                          onChange={modality => setContentSettings(previous => ({ ...previous, [selected]: { ...selectedContent, modality } }))}
                        />
                      </div>
                      <div>
                        <Text type="secondary">语义角色</Text>
                        <Input
                          aria-label="内容语义角色"
                          status={selectedContent.role.trim() ? undefined : 'warning'}
                          value={selectedContent.role}
                          placeholder="例如 article.body.plain"
                          onChange={event => setContentSettings(previous => ({ ...previous, [selected]: { ...selectedContent, role: event.target.value } }))}
                        />
                      </div>
                      {selectedContent.modality === 'text' && (
                        <div>
                          <Text type="secondary">文本格式</Text>
                          <Select<TextFormat>
                            aria-label="文本格式"
                            style={{ width: '100%', marginTop: 4 }}
                            value={selectedContent.format}
                            options={['plain', 'html', 'markdown', 'code'].map(value => ({ value, label: value }))}
                            onChange={format => setContentSettings(previous => ({ ...previous, [selected]: { ...selectedContent, format } }))}
                          />
                        </div>
                      )}
                      <Space>
                        <Switch
                          aria-label="标准化必需字段"
                          checked={selectedContent.requiredForNormalization}
                          onChange={requiredForNormalization => setContentSettings(previous => ({ ...previous, [selected]: { ...selectedContent, requiredForNormalization } }))}
                        />
                        <Text>标准化必需字段</Text>
                      </Space>
                    </Space>
                  </Card>
                )}

                {selectedGroup === 'time_fields' && selected && selectedTime && (
                  <Card size="small" title="时间语义" style={{ marginTop: 12 }}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <div>
                        <Text type="secondary">Canonical 名称</Text>
                        <Input
                          aria-label="时间字段 Canonical 名称"
                          status={selectedTime.targetName.trim() ? undefined : 'warning'}
                          value={selectedTime.targetName}
                          placeholder="例如 published_at"
                          onChange={event => setTimeSettings(previous => ({ ...previous, [selected]: { ...selectedTime, targetName: event.target.value } }))}
                        />
                      </div>
                      <div>
                        <Text type="secondary">源时区</Text>
                        <Select
                          aria-label="源时区"
                          style={{ width: '100%', marginTop: 4 }}
                          value={selectedTime.sourceTimezone}
                          options={[
                            { value: 'unknown', label: '未知（保留原值，不擅自推断）' },
                            { value: 'UTC', label: 'UTC' },
                            { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
                          ]}
                          onChange={sourceTimezone => setTimeSettings(previous => ({ ...previous, [selected]: { ...selectedTime, sourceTimezone } }))}
                        />
                      </div>
                    </Space>
                  </Card>
                )}
              </div>
            ) : <Text type="secondary">请先选择左侧字段</Text>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
              {GROUPS.map(group => (
                <div key={group.value} style={{ padding: 12, border: group.value === 'unassigned' && grouped(group.value).length ? '1px solid #faad14' : '1px solid #e5e5e5', borderRadius: 8, minWidth: 0 }}>
                <Space wrap><Text strong>{group.label}</Text><Tag>{grouped(group.value).length}</Tag></Space>
                <div><Text type="secondary" style={{ fontSize: 12 }}>{group.description}</Text></div>
                <Space wrap size={[6, 8]} style={{ marginTop: 8 }}>
                  {grouped(group.value).length === 0 ? <Text type="secondary">暂无字段</Text> : grouped(group.value).map(f => (
                    <Button key={f.name} size="small" type={selected === f.name ? 'primary' : 'default'} onClick={() => setSelected(f.name)}>{f.name}</Button>
                  ))}
                </Space>
                </div>
              ))}
            </div>
            <Alert
              style={{ marginTop: 12 }}
              type={valid ? 'success' : 'warning'}
              showIcon
              title={valid ? 'Canonical 映射配置完整' : '映射仍需完善'}
              description={valid
                ? '当前仅完成前端配置与校验，尚未提交后端。'
                : `需要恰好一个 sample_id、至少一个内容字段、有效的角色/时间名称，并处理全部未配置字段（当前 ${unassignedCount} 个）。`}
            />
            <Button type="primary" disabled={!valid} loading={saving} onClick={() => void persistMapping()} style={{ marginTop: 12 }}>保存字段映射</Button>
          </Card>
        </div>
        <Card title="数据预览" style={{ width: '100%', minWidth: 0 }} extra={
          <Space wrap>
            <Text type="secondary">预览条数</Text>
            <Select value={limit} style={{ width: 90 }} options={[5, 10, 20].map(n => ({ value: n, label: `${n} 条` }))} onChange={n => { setLimit(n); void loadPreview(n) }} />
            <Button onClick={() => setExpanded(value => !value)}>{expanded ? '收起记录预览' : '查看记录预览'}</Button>
          </Space>
        }>
          {expanded ? (
            <div style={{ width: '100%', maxWidth: '100%', minWidth: 0, overflowX: 'auto' }}>
              <Text type="secondary">展示全部源字段；长文本最多返回 {PREVIEW_TEXT_LENGTH} 个字符。</Text>
              <Table<SourcePreviewRow> style={{ marginTop: 16 }} rowKey={(row, index) => String(row.id ?? index ?? '')} columns={previewColumns} dataSource={rows} loading={previewLoading} pagination={false} tableLayout="fixed" scroll={{ x: Math.max(fields.length * 220, 700) }} />
            </div>
          ) : <Text type="secondary">点击「查看记录预览」展开内部横向滚动的字段表格。</Text>}
        </Card>
      </div>
    </div>
  )
}
