import { recordWork } from '../api/workspace'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import {
  Alert, Button, Card, Col, Collapse, Empty, Form, Input, Modal, Popconfirm, Row, Select, Space, Spin,
  Table, Tag, Typography,
} from 'antd'

import {
  getMappingDraft, getMappingRecommendations, getMappingRevision, getMappingSemantics,
  getRawSamplePage, listMappingRevisions, previewMapping, publishMappingDraft,
  registerMappingSemantic, saveMappingDraft, setDefaultMappingRevision, setMappingConfirmation,
  type DatasetVersion, type RawSamplePage, type SemanticBinding, type SemanticDefinition,
  type SemanticDraft, type SemanticDraftItem, type SemanticPreviewValue, type SemanticRecommendations,
  type SemanticRevision,
} from '../api/datasetVersions'
import { datasetVersionStatusLabel } from '../utils/displayLabels'
import { addRecommendedBindings, semanticBindingKey } from '../utils/semanticDraft'
import RawVersionEvaluation from './RawVersionEvaluation'

export interface RawManifest {
  schema_version: '3.0'
  dataset_version_id: string
  schema_fingerprint: string
}

type Category = 'all' | 'general' | 'domain' | 'custom'
type BindingState = 'unbound' | 'pending' | 'confirmed'
const states: Record<BindingState, string> = { unbound: '未绑定', pending: '待确认', confirmed: '已确认' }
const transformKey = 'html_to_plain@1.0.0'
const representations = ['text/plain', 'text/html', 'json'] as const
const types = ['string', 'object', 'array', 'number', 'boolean', 'any'] as const

function semanticIdentity(definition: SemanticDefinition) {
  return semanticBindingKey({ semantic: definition.key, representation: definition.representation })
}

function bindingState(item?: SemanticDraftItem): BindingState {
  if (!item) return 'unbound'
  return item.confirmed ? 'confirmed' : 'pending'
}

function categoryOf(key: string) {
  if (key.startsWith('custom.')) return 'custom'
  if (key.startsWith('document.')) return 'general'
  return 'domain'
}

function previewText(value: unknown) {
  return typeof value === 'string' ? value || '""' : JSON.stringify(value, null, 2)
}

const codeStyle = {
  whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', overflow: 'auto', maxHeight: 220,
} as const

export default function RawDatasetVersionPanel({ manifest, datasetName, version, onBack }: {
  manifest: RawManifest; datasetName: string; version: DatasetVersion; onBack: () => void
}) {
  const id = manifest.dataset_version_id
  const { pathname } = useLocation()
  const [query, setQuery] = useSearchParams()
  useEffect(() => {
    if (pathname !== '/app/datasets' || query.get('rawVersion') === id) return
    const updated = new URLSearchParams(query)
    updated.set('rawVersion', id)
    setQuery(updated, { replace: true })
  }, [id, pathname, query, setQuery])

  const [draft, setDraft] = useState<SemanticDraft>()
  const [recommendations, setRecommendations] = useState<SemanticRecommendations>()
  const [definitions, setDefinitions] = useState<SemanticDefinition[]>([])
  const [history, setHistory] = useState<SemanticRevision[]>([])
  const [moreHistory, setMoreHistory] = useState(false)
  const [historical, setHistorical] = useState<SemanticRevision>()
  const [page, setPage] = useState<RawSamplePage>()
  const [cursor, setCursor] = useState<string>()
  const [sample, setSample] = useState<string>()
  const [active, setActive] = useState('document.title:text/plain')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<Category>('all')
  const [status, setStatus] = useState<BindingState | 'all'>('all')
  const [preview, setPreview] = useState<SemanticPreviewValue>()
  const [error, setError] = useState('')
  const [sampleError, setSampleError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [initializing, setInitializing] = useState(true)
  const [reload, setReload] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [customOpen, setCustomOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [customForm] = Form.useForm<SemanticDefinition>()
  const inFlight = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getMappingDraft(id, controller.signal), getMappingSemantics(id, controller.signal),
      getMappingRecommendations(id, controller.signal), listMappingRevisions(id, 0, controller.signal),
    ]).then(([saved, semantics, suggested, revisions]) => {
      if (controller.signal.aborted) return
      setDraft(saved)
      setDefinitions(semantics)
      setRecommendations(suggested)
      setHistory(revisions)
      setMoreHistory(revisions.length === 20)
    }).catch((failure: Error) => {
      if (!controller.signal.aborted) setError(failure.message)
    }).finally(() => {
      if (!controller.signal.aborted) setInitializing(false)
    })
    return () => controller.abort()
  }, [id, reload])

  useEffect(() => {
    const controller = new AbortController()
    getRawSamplePage(id, cursor, controller.signal).then((result) => {
      if (controller.signal.aborted) return
      setPage(result)
      setSample(result.samples[0]?.id)
    }).catch((failure: Error) => {
      if (!controller.signal.aborted) setSampleError(failure.message)
    })
    return () => controller.abort()
  }, [id, cursor, reload])

  const items: SemanticDraftItem[] = historical
    ? historical.revision.bindings.map((binding) => ({ binding, confirmed: true }))
    : draft?.items ?? []
  const itemsByIdentity = new Map(items.map((item) => [semanticBindingKey(item.binding), item]))
  const visibleDefinitions = historical
    ? historical.revision.bindings.map((binding): SemanticDefinition => binding.definition
      ?? definitions.find((definition) => semanticIdentity(definition) === semanticBindingKey(binding))
      ?? { key: binding.semantic, representation: binding.representation, name: binding.semantic,
        version: '—', description: '历史修订中的绑定', type: 'any', cardinality: 'one' })
    : definitions
  const selected = visibleDefinitions.find((definition) => semanticIdentity(definition) === active)
    ?? visibleDefinitions[0]
  const item = selected ? itemsByIdentity.get(semanticIdentity(selected)) : undefined
  const confirmed = items.filter((entry) => entry.confirmed).length
  const pending = items.length - confirmed
  const fields = recommendations?.fields ?? []
  const defaultDigest = draft?.default_digest ?? null
  const disabled = Boolean(busy) || initializing
  const revisionOptions = history.map(({ digest }) => ({
    value: digest, label: `${digest.slice(7, 19)}${digest === defaultDigest ? '（默认）' : ''}`,
  }))

  async function perform(label: string, action: () => Promise<void>) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '操作失败')
    } finally {
      inFlight.current = false
      setBusy('')
    }
  }

  function receiveDraft(saved: SemanticDraft) {
    setDraft(saved)
    setDirty(false)
  }

  async function persist() {
    if (draft && dirty) {
      receiveDraft(await saveMappingDraft(id, { items: draft.items }))
      void recordWork('mapping', id)
    }
  }

  function edit(next?: SemanticBinding) {
    if (!draft || !selected || historical) return
    const key = semanticIdentity(selected)
    const others = draft.items.filter((entry) => semanticBindingKey(entry.binding) !== key)
    setDraft({ ...draft, items: next
      ? [...others, { binding: next, semantic_version: selected.version, confirmed: false }]
      : others })
    setDirty(true)
    setPreview(undefined)
  }

  function applyRecommendations() {
    if (!draft || !recommendations) return
    const additions = addRecommendedBindings(draft.items, recommendations.items)
    if (additions.length) {
      setDraft({ ...draft, items: [...draft.items, ...additions] })
      setDirty(true)
      setPreview(undefined)
    }
    setNotice(`已加载 ${recommendations.items.length} 条推荐，新增 ${additions.length} 条待确认绑定。`)
  }

  async function refreshHistory() {
    const revisions = await listMappingRevisions(id)
    setHistory(revisions)
    setMoreHistory(revisions.length === 20)
  }

  const filtered = visibleDefinitions.filter((definition) =>
    (category === 'all' || categoryOf(definition.key) === category)
    && (status === 'all' || bindingState(itemsByIdentity.get(semanticIdentity(definition))) === status)
    && `${definition.name} ${definition.key}`.toLowerCase().includes(search.trim().toLowerCase()))
  const fieldOptions = fields.map((field) => ({ value: field.name, label: `${field.name} · ${field.data_type}` }))

  return <Card title={`${datasetName} · 语义映射`} extra={<Button onClick={onBack}>返回</Button>}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Space wrap>
        <Tag color="blue">SampleRecord v3</Tag>
        <Tag color="green">{datasetVersionStatusLabel(version.status)}</Tag>
        <Typography.Text>数据版本 {version.version}</Typography.Text>
        <Typography.Text type="secondary">{id}</Typography.Text>
        <Typography.Text>{page ? `${page.total.toLocaleString('zh-CN')} 条记录` : '记录数读取中'}</Typography.Text>
      </Space>
      {error && <Alert type="error" title={error} showIcon />}
      {notice && <Alert type="success" title={notice} showIcon />}
      {initializing ? <Space><Spin /><span>读取语义映射</span></Space> : !draft ? <Button onClick={() => {
        setError('')
        setInitializing(true)
        setReload(reload + 1)
      }}>重试读取</Button> : <>
        <Space wrap>
          <Tag color="green">已确认 {confirmed}</Tag>
          <Tag color="gold">待确认 {pending}</Tag>
          <Tag>未绑定 {visibleDefinitions.length - items.length}</Tag>
          <Typography.Text type="secondary">当前视图 {historical ? historical.digest.slice(7, 19) : '草稿'}</Typography.Text>
          <Typography.Text type="secondary">默认修订 {defaultDigest?.slice(7, 19) ?? '未设置'}</Typography.Text>
          <Select aria-label="历史映射修订" placeholder="查看历史修订" style={{ minWidth: 200 }}
            value={historical?.digest} allowClear disabled={disabled} options={revisionOptions}
            onChange={(digest) => {
              if (!digest) { setHistorical(undefined); setPreview(undefined); return }
              void perform('history', async () => {
                setHistorical(await getMappingRevision(id, digest))
                setPreview(undefined)
                setSearch('')
                setCategory('all')
                setStatus('all')
              })
            }} />
          {moreHistory && <Button disabled={disabled} onClick={() => void perform('history', async () => {
            const next = await listMappingRevisions(id, history.length)
            setHistory([...history, ...next])
            setMoreHistory(next.length === 20)
          })}>加载更多修订</Button>}
        </Space>
        {historical ? <Space wrap>
          <Typography.Text>历史修订只读</Typography.Text>
          <Popconfirm title="用此修订替换当前草稿？" description="修订中的绑定需要重新确认。"
            disabled={disabled || dirty} onConfirm={() => perform('fork', async () => {
              receiveDraft(await saveMappingDraft(id, { from_digest: historical.digest }))
              setHistorical(undefined)
              setPreview(undefined)
              setNotice('已从历史修订创建草稿，绑定待重新确认。')
            })}>
            <Button disabled={disabled || dirty} title={dirty ? '请先返回草稿并保存' : undefined}>基于此修订编辑</Button>
          </Popconfirm>
          <Button disabled={disabled || historical.digest === defaultDigest}
            onClick={() => void perform('default', async () => {
              const result = await setDefaultMappingRevision(id, historical.digest)
              setDraft({ ...draft, ...result })
              setNotice('已设置默认修订。')
            })}>设为默认修订</Button>
          <Button disabled={disabled} onClick={() => { setHistorical(undefined); setPreview(undefined) }}>返回草稿</Button>
        </Space> : <Space wrap>
          <Button disabled={disabled || !recommendations?.items.length} onClick={applyRecommendations}>
            应用推荐（{recommendations?.items.length ?? 0}）
          </Button>
          <Button disabled={disabled || pending === 0} loading={busy === 'confirmAll'}
            onClick={() => void perform('confirmAll', async () => {
              receiveDraft(await saveMappingDraft(id, { items: draft.items, confirm_all: true }))
              setNotice(`已确认 ${pending} 条绑定并保存草稿。`)
            })}>
            一键确认待确认绑定（{pending}）
          </Button>
          <Button disabled={disabled} onClick={() => setCustomOpen(true)}>登记自定义语义</Button>
          {recommendations?.provenance && <Typography.Text type="secondary">
            {recommendations.provenance.template_id}@{recommendations.provenance.template_version}
            { ` · Schema 匹配 ${Math.round(recommendations.provenance.match_score * 100)}%` }
          </Typography.Text>}
        </Space>}
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={10} xl={8}>
            <Card size="small" title="目标语义">
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Input aria-label="搜索语义" placeholder="搜索名称或语义键" value={search}
                  onChange={(event) => setSearch(event.target.value)} allowClear />
                <Space wrap>
                  <Select aria-label="语义分类" value={category} onChange={setCategory} options={[
                    { value: 'all', label: '全部分类' }, { value: 'general', label: '通用' },
                    { value: 'domain', label: '领域' }, { value: 'custom', label: '自定义' },
                  ]} />
                  <Select aria-label="确认状态筛选" value={status} onChange={setStatus} options={[
                    { value: 'all', label: '全部状态' },
                    ...Object.entries(states).map(([value, label]) => ({ value, label })),
                  ]} />
                </Space>
                {filtered.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的语义" />
                  : <ul aria-label="目标语义" style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: 410, overflow: 'auto' }}>
                    {filtered.map((definition) => {
                      const key = semanticIdentity(definition)
                      const current = bindingState(itemsByIdentity.get(key))
                      const focused = selected && key === semanticIdentity(selected)
                      return <li key={key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '4px 0' }}>
                        <Button type={focused ? 'primary' : 'text'} aria-pressed={Boolean(focused)}
                          disabled={disabled} onClick={() => { setActive(key); setPreview(undefined) }}>
                          {definition.name}
                        </Button>
                        <Tag color={current === 'confirmed' ? 'green' : current === 'pending' ? 'gold' : undefined}>
                          {states[current]}
                        </Tag>
                      </li>
                    })}
                  </ul>}
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={14} xl={16}>
            {selected ? <Card size="small" title={selected.name} extra={<Tag>{states[bindingState(item)]}</Tag>}>
              <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
                <Typography.Text code>{selected.key} · {selected.version}</Typography.Text>
                <Typography.Paragraph style={{ margin: 0 }}>{selected.description}</Typography.Paragraph>
                <Space wrap>
                  <Tag>{selected.type}</Tag><Tag>{selected.representation}</Tag>
                  <Tag>{selected.cardinality === 'one' ? '单值' : '多值'}</Tag>
                </Space>
                <Space wrap>
                  <Select aria-label="来源字段" placeholder="选择来源字段" showSearch optionFilterProp="label"
                    style={{ minWidth: 240 }} value={item?.binding.field_tokens[0]} options={fieldOptions}
                    disabled={disabled || Boolean(historical)} allowClear onChange={(field?: string) => {
                      edit(field ? {
                        semantic: selected.key, representation: selected.representation, field_tokens: [field],
                        transform: item?.binding.transform, source_representation: item?.binding.source_representation,
                      } : undefined)
                    }} />
                  <Select aria-label="版本化 Transform" style={{ minWidth: 228 }}
                    value={item?.binding.transform ?? 'direct'} disabled={disabled || !item || Boolean(historical)}
                    onChange={(transform: string) => {
                      if (!item) return
                      const derived = transform === transformKey
                      edit({
                        semantic: selected.key, representation: selected.representation,
                        field_tokens: item.binding.field_tokens,
                        transform: derived ? transformKey : null, source_representation: derived ? 'text/html' : null,
                      })
                    }}
                    options={[
                      { value: 'direct', label: '直接映射' },
                      ...(selected.representation === 'text/plain'
                        ? [{ value: transformKey, label: 'HTML → 纯文本 · 1.0.0' }] : []),
                      ...(item?.binding.transform && item.binding.transform !== transformKey
                        ? [{ value: item.binding.transform, label: item.binding.transform }] : []),
                    ]} />
                </Space>
                {item?.binding.field_tokens.length && item.binding.field_tokens.length > 1
                  ? <Typography.Text code>{item.binding.field_tokens.join(' / ')}</Typography.Text> : null}
                {item?.rationale && <Typography.Text type="secondary">{item.rationale}</Typography.Text>}
                {sampleError && <Alert type="error" title={sampleError} />}
                <Space wrap>
                  <Select aria-label="预览样本" placeholder="选择预览样本" style={{ minWidth: 320 }}
                    value={sample} disabled={disabled} loading={!page && !sampleError}
                    onChange={(value) => { setSample(value); setPreview(undefined) }}
                    options={page?.samples.map(({ id }) => ({ value: id, label: id }))} />
                  <Button disabled={disabled || !sample} loading={busy === 'preview'}
                    onClick={() => void perform('preview', async () => {
                      if (!sample) return
                      setPreview(undefined)
                      if (!historical) await persist()
                      const result = await previewMapping(id, sample, {
                        name: 'value', semantic: selected.key, representation: selected.representation,
                        value_type: selected.type, cardinality: selected.cardinality,
                      }, historical?.digest)
                      setPreview(result.values.value)
                    })}>预览映射</Button>
                  {!historical && <Button type="primary" disabled={disabled || !item} loading={busy === 'confirm'}
                    onClick={() => void perform('confirm', async () => {
                      if (!item) return
                      await persist()
                      receiveDraft(await setMappingConfirmation(id, item.binding, !item.confirmed))
                    })}>{item?.confirmed ? '取消确认' : '确认绑定'}</Button>}
                </Space>
                {preview && <Card size="small" title="真实样本解析"
                  extra={<Tag color={preview.status === 'PRESENT' ? 'green' : 'gold'}>{preview.status}</Tag>}>
                  <Typography.Text type="secondary">
                    {preview.field_tokens?.join(' / ') ?? '无来源路径'} · {preview.source_representation ?? '—'} → {preview.representation}
                  </Typography.Text>
                  {preview.diagnostics.length > 0 && <Alert type="info" title={`诊断：${preview.diagnostics.join('、')}`} />}
                  <Row gutter={[16, 8]}>
                    <Col xs={24} md={12}><Typography.Text strong>原始值</Typography.Text>
                      <pre style={codeStyle}>{previewText(preview.raw_value)}</pre></Col>
                    <Col xs={24} md={12}><Typography.Text strong>解析值</Typography.Text>
                      <pre style={codeStyle}>{previewText(preview.value)}</pre></Col>
                  </Row>
                </Card>}
              </Space>
            </Card> : <Empty description="没有可查看的语义" />}
          </Col>
        </Row>
        <Collapse items={[
          { key: 'schema', label: `原始字段 · ${fields.length} 个`, children:
            <Table rowKey="name" size="small" dataSource={fields} pagination={false}
              columns={[{ title: '字段', dataIndex: 'name' }, { title: '类型', dataIndex: 'data_type' },
                { title: '可空', render: (_, field) => field.nullable ? '是' : '否' }]} /> },
          { key: 'raw', label: '原始数据预览', children:
            <Space orientation="vertical" style={{ width: '100%' }}>
              {sampleError && <Alert type="error" title={sampleError} />}
              <Table rowKey="id" size="small" pagination={false} dataSource={page?.samples}
                loading={!page && !sampleError} columns={[{ title: '样本 ID', dataIndex: 'id' }]}
                expandable={{ expandedRowRender: (row) => <pre style={codeStyle}>{JSON.stringify(row.data, null, 2)}</pre> }} />
              <Space>
                <Button disabled={!cursor || disabled} onClick={() => {
                  setCursor(undefined); setPage(undefined); setSample(undefined); setPreview(undefined); setSampleError('')
                }}>第一页</Button>
                <Button disabled={!page?.next_cursor || disabled} onClick={() => {
                  setCursor(page?.next_cursor ?? undefined); setPage(undefined); setSample(undefined); setPreview(undefined); setSampleError('')
                }}>下一页</Button>
              </Space>
            </Space> },
        ]} />
        {!historical && <Space wrap>
          <Button disabled={disabled || !dirty} loading={busy === 'save'} onClick={() => void perform('save', async () => {
            await persist()
            setNotice('草稿已保存。')
          })}>保存草稿</Button>
          <Button type="primary" disabled={disabled || dirty || confirmed === 0}
            onClick={() => setPublishOpen(true)}>发布映射修订</Button>
          {dirty && <Typography.Text type="warning">有未保存的编辑。预览或确认绑定也会先保存草稿。</Typography.Text>}
        </Space>}
        <Collapse defaultActiveKey={query.get('view') === 'single' ? ['evaluation'] : []} items={[{ key: 'evaluation', label: '正文完整度评测与报告', children:
          <RawVersionEvaluation versionId={id} history={history} defaultDigest={defaultDigest} /> }]} />
      </>}
    </Space>
    <Modal title="登记自定义语义" open={customOpen} onCancel={() => setCustomOpen(false)}
      confirmLoading={busy === 'custom'} onOk={() => customForm.submit()}>
      <Form form={customForm} layout="vertical"
        initialValues={{ version: '1.0.0', type: 'string', representation: 'text/plain', cardinality: 'one' }}
        onFinish={(definition) => void perform('custom', async () => {
          try {
            const saved = await registerMappingSemantic(id, definition)
            setDefinitions([...definitions, saved])
            setActive(semanticIdentity(saved))
            setPreview(undefined)
            setCustomOpen(false)
            customForm.resetFields()
          } catch (failure) {
            customForm.setFields([{ name: 'key', errors: [failure instanceof Error ? failure.message : '登记失败'] }])
          }
        })}>
        <Form.Item name="key" label="语义键" rules={[{ required: true, pattern: /^custom\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/, message: '使用 custom. 前缀和小写语义键' }]}><Input placeholder="custom.example" /></Form.Item>
        <Form.Item name="version" label="版本" rules={[{ required: true, pattern: /^\d+\.\d+\.\d+$/, message: '使用主版本.次版本.修订号' }]}><Input /></Form.Item>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="description" label="说明" rules={[{ required: true }]}><Input.TextArea /></Form.Item>
        <Form.Item name="type" label="类型"><Select options={types.map((value) => ({ value }))} /></Form.Item>
        <Form.Item name="representation" label="表示"><Select options={representations.map((value) => ({ value }))} /></Form.Item>
        <Form.Item name="cardinality" label="基数"><Select options={[{ value: 'one', label: '单值' }, { value: 'many', label: '多值' }]} /></Form.Item>
      </Form>
    </Modal>
    <Modal title="发布映射修订" open={publishOpen} onCancel={() => setPublishOpen(false)}
      footer={(_, { CancelBtn }) => <>
        <CancelBtn />
        <Button disabled={disabled} onClick={() => void publish(false)}>仅发布</Button>
        <Button type="primary" disabled={disabled} onClick={() => void publish(true)}>发布并设为默认</Button>
      </>}>
      <Typography.Paragraph>已确认 {confirmed} 项，待确认 {pending} 项。正式修订仅包含已确认绑定。数据版本已独立发布。</Typography.Paragraph>
    </Modal>
  </Card>

  async function publish(makeDefault: boolean) {
    await perform('publish', async () => {
      const result = await publishMappingDraft(id, makeDefault)
      setPublishOpen(false)
      setDraft((current) => current && ({ ...current, default_digest: result.default_digest }))
      await refreshHistory()
      setNotice(`已发布修订 ${result.digest.slice(7, 19)}。`)
    })
  }
}
