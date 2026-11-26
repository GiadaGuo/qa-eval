import ParameterInput from '../components/ParameterInput'
import { calculatorDefaults, updateCalculatorParameters } from '../utils/calculatorDefaults'
import ContractInputEditor from '../components/ContractInputEditor'
import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Checkbox, Collapse, Descriptions, Form, Input, InputNumber, Modal,
  Result, Select, Space, Spin, Statistic, Table, Tag, Typography,
} from 'antd'

import { getMappingDraft, getMappingRecommendations, listMappingRevisions, listDatasets, listDatasetVersions, type DatasetVersion } from '../api/datasetVersions'
import {
  getPublishedQualityContract,
  preflightContractInputs,
  publishNativeQualityContract,
  publishQualityContract,
  publishOperatorContract,
  type PublishedQualityContract,
  type QualityContractPublishRequest,
  type RuleOperator,
  type Severity,
} from '../api/qualityContracts'
import type { ParameterSchema } from '../api/registry'
import type { ContractSeed } from './MetricRegistryPage'
import { createEvaluationRun, getEvaluationRun, getRunMetricResults, type EvaluationRun, type MetricResult } from '../api/evaluationRuns'
import { evaluationRunStatusLabel, isEvaluationRunTerminal } from '../utils/evaluationRunStatus'
import {
  datasetVersionStatusLabel,
  onMissingLabel,
  severityLabel,
} from '../utils/displayLabels'

const { Title, Text } = Typography

interface Props {
  initialVersionId?: string
  seed: ContractSeed
  onBack: () => void
}

type FormValues = {
  name: string
  version_label: string
  dataset_version_id: string
  measurement: string
  strategy: "EVALUATE" | "OBSERVE_ONLY"
  operator: RuleOperator
  threshold: number | string
  severity: Severity
  on_missing: 'UNKNOWN' | 'SKIPPED'
  parameters: Record<string, unknown>
  enabledOperators?: Record<string, boolean>
  operatorChoices?: Record<string, string>
  operatorParameters?: Record<string, string>
}

type Overrides = Record<string, Record<string, number>>

function NumericParameter({ schema, value, onChange, label }: {
  schema: ParameterSchema; value?: number; onChange?: (value: number | undefined) => void; label?: string
}) {
  const scale = schema.display_unit === 'percent' ? 100 : 1
  return <InputNumber aria-label={label ?? schema.title} style={{ width: '100%' }}
    min={schema.minimum === undefined ? undefined : schema.minimum * scale}
    max={schema.maximum === undefined ? undefined : schema.maximum * scale}
    precision={schema.type === 'integer' ? 0 : undefined}
    suffix={scale === 100 ? '%' : undefined}
    value={value == null ? null : value * scale}
    onChange={next => onChange?.(next === null ? undefined : next / scale)} />
}

function ParameterOverrides({ schema, value = {}, onChange }: {
  schema: ParameterSchema; value?: Overrides; onChange?: (value: Overrides) => void
}) {
  const properties = typeof schema.additionalProperties === 'object' ? schema.additionalProperties.properties : undefined
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Select aria-label="添加文种" placeholder="添加文种" value={null} style={{ minWidth: 160 }}
      options={schema.propertyNames?.enum?.filter(type => !Object.hasOwn(value, type)).map(type => ({ value: type }))}
      onChange={type => { if (typeof type === 'string') onChange?.({ ...value, [type]: {} }) }} />
    {Object.entries(value).map(([type, overrides]) => <Card key={type} size="small" title={type}
      extra={<Button aria-label={`删除文种 ${type}`} onClick={() => {
        const next = { ...value }
        delete next[type]
        onChange?.(next)
      }}>删除</Button>}>
      <Space wrap>{Object.entries(properties ?? {}).map(([key, definition]) =>
        <label key={key}>{definition.title ?? key}
          <NumericParameter schema={definition} label={`${type} ${definition.title ?? key}`} value={overrides[key]}
            onChange={number => {
              const next = { ...overrides }
              if (number === undefined) delete next[key]
              else next[key] = number
              onChange?.({ ...value, [type]: next })
            }} />
        </label>)}</Space>
    </Card>)}
  </Space>
}

export default function QualityContractBuilderPage({ seed, onBack, initialVersionId }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [versions, setVersions] = useState<Array<DatasetVersion & { dataset_name: string }>>([])
  const [loadingVersions, setLoadingVersions] = useState(true)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [published, setPublished] = useState<PublishedQualityContract | null>(null)
  const [run, setRun] = useState<EvaluationRun | null>(null)
  const [evidencePreview, setEvidencePreview] = useState<string | null>(null)
  const [results, setResults] = useState<MetricResult[]>([])

  const [mapping, setMapping] = useState<{
    id: string
    draft: Awaited<ReturnType<typeof getMappingDraft>>
    revisions: Awaited<ReturnType<typeof listMappingRevisions>>
    recommendations: Awaited<ReturnType<typeof getMappingRecommendations>>
  }>()
  const [refreshMappings, setRefreshMappings] = useState(0)
  const [inputCheck, setInputCheck] = useState<Awaited<ReturnType<typeof preflightContractInputs>>>()
  const [checking, setChecking] = useState(false)
  const [restoreId, setRestoreId] = useState('')
  const versionId = Form.useWatch('dataset_version_id', form)
  const raw = versions.find((version) => version.id === versionId)?.record_schema === '3.0'
  const [mappingDigest, setMappingDigest] = useState<string>()
  const [slotChoices, setSlotChoices] = useState<Record<string, string>>({})
  const currentMapping = mapping?.id === versionId ? mapping : undefined
  const chosenDigest = mappingDigest ?? currentMapping?.draft.default_digest ?? undefined

  useEffect(() => {
    if (!raw || !versionId) return
    const controller = new AbortController()
    Promise.all([
      getMappingDraft(versionId, controller.signal), listMappingRevisions(versionId, 0, controller.signal),
      getMappingRecommendations(versionId, controller.signal),
    ]).then(([draft, revisions, recommendations]) => {
      if (!controller.signal.aborted) setMapping({ id: versionId, draft, revisions, recommendations })
    }).catch((failure: Error) => {
      if (!controller.signal.aborted) setError(failure.message)
    })
    return () => controller.abort()
  }, [raw, versionId, refreshMappings])

  const preflight = async (values: FormValues) => {
    const result = await preflightContractInputs({
      dataset_version_id: values.dataset_version_id,
      metric_key: seed.metric.metric_key, metric_version: seed.metric.version,
      calculator_key: seed.calculator.calculator_key, calculator_version: seed.calculator.version,
      mapping_digest: chosenDigest, parameters: values.parameters ?? {},
      input_selectors: Object.fromEntries(Object.entries(slotChoices).map(([name, value]) => [
        name, value.startsWith('field:') ? { field_tokens: [value.slice(6)] } : { semantic: value.slice(9) },
      ])),
    })
    setInputCheck(result)
    return result
  }

  const defaultParameters = useMemo(() => calculatorDefaults(seed.calculator), [seed.calculator])

  const producedMeasurements = useMemo(() => {
    const produced = seed.binding.produced_measurements
    if (!produced || produced.length === 0) return undefined
    return new Set(produced)
  }, [seed.binding.produced_measurements])

  const availableMeasurements = useMemo(() => {
    if (producedMeasurements === undefined) return seed.metric.measurements
    return seed.metric.measurements.filter(item => producedMeasurements.has(item.key))
  }, [seed.metric.measurements, producedMeasurements])

  useEffect(() => {
    let cancelled = false
    listDatasets()
      .then(async datasets => {
        const groups = await Promise.all(datasets.map(async dataset => {
          const items = await listDatasetVersions(dataset.id)
          return items.map(version => ({ ...version, dataset_name: dataset.name }))
        }))
        if (!cancelled) setVersions(groups.flat().filter(version => version.status === 'PUBLISHED'))
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '加载数据集版本失败')
      })
      .finally(() => { if (!cancelled) setLoadingVersions(false) })
    return () => { cancelled = true }
  }, [])

  const operators = seed.operators ?? []
  const slotted = operators.length > 0
  const operatorChoices = Form.useWatch('operatorChoices', form)
  const enabledOperators = Form.useWatch('enabledOperators', form)
  const slots = [...new Set(operators.map(entry => entry.binding.compatibility?.operator_slot ?? ''))]
  const identityOf = (entry: typeof operators[number]) => `${entry.calculator.calculator_key}@${entry.calculator.version}`

  const native = !!seed.binding.projections
  const compatibility = seed.binding.compatibility
  const defaultRule = compatibility?.default_rule
  const primary = compatibility?.primary_measurement
  const statistics = seed.calculator.native_output?.statistics.filter(item => ['count', 'ratio', 'scalar'].includes(item.kind)) ?? []
  const defaultMeasurement = native
    ? (primary ?? (statistics.find(item => item.key === 'expired_evidence_rate') ?? statistics[0])?.key)
    : (primary ?? (availableMeasurements.find(item => item.value_type === 'NUMBER') ?? availableMeasurements[0])?.key)
  const defaultDirection = availableMeasurements.find(item => item.key === defaultMeasurement)?.direction
  const defaultOperator = defaultRule?.operator ?? (defaultDirection === 'HIGHER_IS_BETTER' ? 'GTE' : 'LTE')
  const strategy = Form.useWatch('strategy', form)
  const showThreshold = !native || strategy !== 'OBSERVE_ONLY'
  const measurement = Form.useWatch('measurement', form)
  const selectedMeasurement = availableMeasurements.find(item => item.key === measurement)
  const selectedStatistic = statistics.find(item => item.key === measurement)
  const numericThreshold = native || selectedMeasurement?.value_type === 'NUMBER'
  const minimum = native ? selectedStatistic?.minimum ?? undefined : selectedMeasurement?.minimum
  const maximum = native ? selectedStatistic?.maximum ?? undefined : selectedMeasurement?.maximum

  const submit = async (values: FormValues) => {
    if (!slotted && !values.measurement) {
      setError('该算子未配置可用的判定指标')
      return
    }
    setPublishing(true)
    setError(null)
    const payload: QualityContractPublishRequest = {
      name: values.name,
      version_label: values.version_label,
      dataset_version_id: values.dataset_version_id,
      metric_key: seed.metric.metric_key,
      metric_version: seed.metric.version,
      measurement: values.measurement,
      calculator_key: seed.calculator.calculator_key,
      calculator_version: seed.calculator.version,
      parameters: values.parameters ?? {},
      rule: {
        operator: values.operator,
        threshold: values.threshold,
        severity: values.severity,
        on_missing: values.on_missing,
      },
    }
    try {
      if (raw) {
        if ((native && !primary) || slotted) throw new Error('当前 v3 配置入口支持直接测量算子')
        const check = await preflight(values)
        if (check.status !== 'READY') throw new Error(check.detail ?? check.status)
        payload.mapping_digest = check.plan?.revision_fingerprint || undefined
        payload.input_selectors = check.plan ? Object.fromEntries(Object.entries(slotChoices).map(([name, value]) => [
          name, value.startsWith('field:') ? { field_tokens: [value.slice(6)] } : { semantic: value.slice(9) },
        ])) : undefined
      }
      if (slotted) {
        const items = slots.flatMap(slot => {
          if (!values.enabledOperators?.[slot]) return []
          const matches = operators.filter(value => value.binding.compatibility?.operator_slot === slot)
          const preferred = !values.operatorChoices?.[slot] || values.operatorChoices[slot] === 'preferred'
          const entry = preferred ? matches[0] : matches.find(value => identityOf(value) === values.operatorChoices?.[slot])
          if (!entry) return []
          const parameters = JSON.parse(values.operatorParameters?.[slot] || '{}') as Record<string, unknown>
          return [{
            key: slot,
            operator_slot: slot,
            metric_key: payload.metric_key,
            metric_version: payload.metric_version,
            measurement: entry.binding.compatibility?.primary_measurement ?? '',
            ...(preferred ? {} : {
              calculator_key: entry.calculator.calculator_key,
              calculator_version: entry.calculator.version,
            }),
            parameters,
            rule: { strategy: 'OBSERVE_ONLY' as const, severity: 'WARNING' as const, on_missing: 'UNKNOWN' as const },
          }]
        })
        if (!items.length) throw new Error('请选择至少一个算子槽位')
        const { name, version_label, dataset_version_id } = payload
        setPublished(await publishOperatorContract({ name, version_label, dataset_version_id }, items))
      } else if (native) {
        const key = seed.calculator.calculator_key
        const common = {
          key, projections: [] as [],
          metric_key: payload.metric_key, metric_version: payload.metric_version,
          calculator_key: payload.calculator_key, calculator_version: payload.calculator_version,
          parameters: payload.parameters,
          mapping_digest: payload.mapping_digest, input_selectors: payload.input_selectors,
        }
        const identity = { name: payload.name, version_label: payload.version_label, dataset_version_id: payload.dataset_version_id }
        setPublished(await publishNativeQualityContract(identity, values.strategy === 'OBSERVE_ONLY'
          ? { ...common, schema_version: '1.0', strategy: 'OBSERVE_ONLY' }
          : {
            ...common, schema_version: '1.1', strategy: 'EVALUATE',
            statistic_ref: { contract_item_key: key, output_slot_id: key, calculator_version: payload.calculator_version, statistics_key: values.measurement },
            rule: { operator: values.operator, threshold: values.threshold },
            severity: values.severity,
          }))
      } else {
        setPublished(await publishQualityContract(payload))
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '发布质量契约失败')
    } finally {
      setPublishing(false)
    }
  }

  const openEvidence = async (evidenceId: string) => {
    setError(null)
    const base = `/api/v1/evidence/${evidenceId}`
    try {
      const response = await fetch(`${base}/index/rebuild`, { method: 'POST' })
      if (!response.ok) throw new Error(`证据索引加载失败（HTTP ${response.status}）`)
      const rows = await fetch(`${base}/rows`)
      if (!rows.ok) throw new Error(`证据读取失败（HTTP ${rows.status}）`)
      setEvidencePreview(JSON.stringify(await rows.json(), null, 2))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '证据读取失败')
    }
  }

  const startEvaluation = async () => {
    if (!published) return
    setPublishing(true)
    setError(null)
    try { setRun(await createEvaluationRun(published.contract.contract_version_id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '创建评测任务失败') }
    finally { setPublishing(false) }
  }

  useEffect(() => {
    if (!run || isEvaluationRunTerminal(run.status)) return
    let cancelled = false
    let timer: number | undefined
    let consecutiveErrors = 0
    const poll = () => {
      getEvaluationRun(run.run_id).then(next => {
        if (cancelled) return
        consecutiveErrors = 0
        setRun(next)
        if (isEvaluationRunTerminal(next.status)) {
          getRunMetricResults(next.run_id).then(setResults).catch(reason => setError(reason instanceof Error ? reason.message : '读取指标结果失败'))
          return
        }
        timer = window.setTimeout(poll, 1000)
      }).catch(reason => {
        if (cancelled) return
        consecutiveErrors += 1
        setError(reason instanceof Error ? reason.message : '查询评测任务失败')
        if (consecutiveErrors < 8) timer = window.setTimeout(poll, Math.min(30000, 1000 * 2 ** consecutiveErrors))
      })
    }
    timer = window.setTimeout(poll, 1000)
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [run])

  if (published) return <Result
    status="success"
    icon={<CheckCircleOutlined />}
    title="质量契约版本已发布"
    subTitle={`${published.contract.name} · ${published.contract.version_label}`}
    extra={<Space direction="vertical" style={{ width: '100%' }}>
      <Descriptions bordered size="small" column={1} style={{ textAlign: 'left', maxWidth: 900, margin: '0 auto' }}>
        <Descriptions.Item label="契约版本 ID">{published.contract.contract_version_id}</Descriptions.Item>
        <Descriptions.Item label="数据集版本 ID">{published.target_dataset_version_id}</Descriptions.Item>
        {published.contract.items.map((item) => {
          const inputs = item.calculation?.implementation_constraints.attributes
          return inputs?.mapping_digest || inputs?.input_selectors ? <Descriptions.Item key={item.key} label="已保存的评测输入">
            {inputs.mapping_digest && <div>{inputs.mapping_digest}</div>}
            {Object.entries(inputs.input_selectors ?? {}).map(([name, selector]) => <div key={name}>
              {name} · {'field_tokens' in selector ? selector.field_tokens.join(' / ') : selector.semantic}
            </div>)}
          </Descriptions.Item> : null
        })}
        {published.contract.observation_items?.map(item => <Descriptions.Item key={item.key} label="已保存的原生评测配置">
          {item.statistic_ref && <div>{item.statistic_ref.statistics_key} · {item.rule?.operator} {item.rule?.threshold}</div>}
          <pre>{JSON.stringify(item.calculation.parameters, null, 2)}</pre>
        </Descriptions.Item>)}
        <Descriptions.Item label="内容指纹（Fingerprint）">{published.contract.fingerprint}</Descriptions.Item>
        <Descriptions.Item label="状态"><Tag color="success">{datasetVersionStatusLabel(published.contract.status)}</Tag></Descriptions.Item>
      </Descriptions>
      {error && <Alert type="error" showIcon title="评测任务处理失败" description={error} />}
      {!run ? <Button type="primary" loading={publishing} onClick={() => void startEvaluation()}>开始评测</Button> : <Card title="评测任务" style={{ textAlign: 'left' }}>
        <Space wrap size="large"><Statistic title="任务 ID" value={run.run_id} /><Statistic title="执行状态" value={evaluationRunStatusLabel(run.status)} /></Space>
        {results.map(result => <Card key={result.metric_result_id} size="small" title={`${result.status_detail?.calculator?.key ?? result.metric.key} · ${result.status}`}>
          <Table style={{ marginTop: 16 }} rowKey="key" pagination={false} dataSource={result.observations} columns={[{ title: '测量项', dataIndex: 'key' }, { title: '值', dataIndex: 'value', render: value => String(value) }, { title: '单位', dataIndex: 'unit', render: value => value ?? '—' }]} />
          <Space wrap>{result.evidence.map(evidence => <Button key={evidence.evidence_id} type="link" onClick={() => void openEvidence(evidence.evidence_id)}>{evidence.kind}</Button>)}</Space>
        </Card>)}
      </Card>}
      <Modal title="证据预览" open={evidencePreview !== null} footer={null} onCancel={() => setEvidencePreview(null)} width={760}>
        <pre style={{ overflow: 'auto', maxHeight: '60vh' }}>{evidencePreview}</pre>
      </Modal>
      <Button onClick={onBack}>返回指标管理</Button>
    </Space>}
  />

  const parameterProperties = seed.calculator.parameter_schema.properties ?? {}
  return <div style={{ width: '100%', maxWidth: 1080, margin: '0 auto', textAlign: 'left' }}>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div><Button icon={<ArrowLeftOutlined />} onClick={onBack}>返回指标管理</Button><Title level={2} style={{ marginBottom: 4, marginTop: 18 }}>构建质量契约</Title><Text type="secondary">规则由用户配置；平台 ID、fingerprint、发布状态和发布时间由后端生成。</Text></div>
      {error && <Alert type="error" showIcon title="质量契约处理失败" description={error} />}
      <Card title="已选 Registry 对象">
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="指标">{seed.metric.metric_key} · {seed.metric.name}@{seed.metric.version}</Descriptions.Item>
          <Descriptions.Item label="Calculator">{seed.calculator.name}@{seed.calculator.version}</Descriptions.Item>
          <Descriptions.Item label="Binding 优先级">{seed.binding.priority}</Descriptions.Item>
          <Descriptions.Item label="Binding 状态"><Tag color={seed.binding.enabled ? 'success' : 'default'}>{seed.binding.enabled ? '启用' : '停用'}</Tag></Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="契约与规则配置">
        <Space.Compact style={{ marginBottom: 16 }}>
          <Input aria-label="已发布契约 ID" placeholder="已发布契约 ID" value={restoreId} onChange={(event) => setRestoreId(event.target.value)} />
          <Button disabled={!restoreId.trim()} loading={publishing} onClick={() => {
            setPublishing(true)
            setError(null)
            void getPublishedQualityContract(restoreId.trim()).then(setPublished)
              .catch((failure: Error) => setError(failure.message)).finally(() => setPublishing(false))
          }}>读取已发布契约</Button>
        </Space.Compact>
        {loadingVersions ? <Spin /> : <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={{ dataset_version_id: initialVersionId, name: `${seed.metric.name}质量契约`, version_label: 'v1.0.0', strategy: 'EVALUATE', measurement: defaultMeasurement, operator: defaultOperator, threshold: defaultRule?.threshold, severity: 'ERROR', on_missing: 'UNKNOWN', parameters: defaultParameters }}
          onValuesChange={(changes: Partial<FormValues>, values: FormValues) => {
            if (changes.parameters && (Object.hasOwn(changes.parameters, 'dictionary') || Object.hasOwn(changes.parameters, 'dictionary_path'))) {
              form.setFieldValue('parameters', updateCalculatorParameters(values.parameters ?? {}, changes.parameters))
            }
            setInputCheck(undefined)
          }}
          onFinish={submit}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            <Form.Item name="name" label="契约名称" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="version_label" label="版本标签" rules={[{ required: true }]}><Input /></Form.Item>
          </div>
          <Form.Item name="dataset_version_id" label="目标数据集版本" rules={[{ required: true, message: '请选择已发布的数据集版本' }]}>
            <Select onChange={() => { setMappingDigest(undefined); setSlotChoices({}); setInputCheck(undefined) }} showSearch optionFilterProp="label" placeholder={versions.length ? '选择已发布版本' : '没有可用的已发布版本'} options={versions.map(version => ({ value: version.id, label: `${version.dataset_name} / ${version.version} (${version.id})` }))} />
          </Form.Item>
          {raw && <ContractInputEditor calculatorKey={seed.calculator.calculator_key} versionId={versionId} currentMapping={currentMapping}
            mappingDigest={mappingDigest} chosenDigest={chosenDigest} setMappingDigest={setMappingDigest}
            slots={seed.calculator.input_contract.logical?.slots} slotChoices={slotChoices}
            setSlotChoices={setSlotChoices} inputCheck={inputCheck} setInputCheck={setInputCheck}
            checking={checking} onRefresh={() => { setRefreshMappings(value => value + 1); setInputCheck(undefined) }}
            onCheck={() => {
              setChecking(true)
              void preflight(form.getFieldsValue()).catch((failure: Error) => setError(failure.message))
                .finally(() => setChecking(false))
            }} />}
          {slotted && slots.map(slot => {
            const matches = operators.filter(entry => entry.binding.compatibility?.operator_slot === slot)
            const selected = !operatorChoices?.[slot] || operatorChoices[slot] === 'preferred' ? matches[0] : matches.find(entry => identityOf(entry) === operatorChoices?.[slot])
            return <Card key={slot} title={slot} size="small">
              <Form.Item name={['enabledOperators', slot]} valuePropName="checked">
                <Checkbox>启用</Checkbox>
              </Form.Item>
              {enabledOperators?.[slot] && <Form.Item name={['operatorChoices', slot]} label="实现">
                <Select allowClear placeholder="按优先级选择" options={[{ value: 'preferred', label: '按优先级选择' }, ...matches.map(entry => ({
                  value: identityOf(entry), label: `${identityOf(entry)} · 优先级 ${entry.binding.priority}`,
                }))]} />
              </Form.Item>}
              {enabledOperators?.[slot] && selected && <Form.Item name={['operatorParameters', slot]} label="参数 JSON"
                extra={Object.keys(selected.calculator.parameter_schema.properties ?? {}).join('、')}>
                <Input.TextArea rows={3} placeholder={Object.hasOwn(selected.calculator.parameter_schema.properties ?? {}, 'fields') ? '{"fields":[{"selector":"content_role","role":"article.body.plain"}]}' : '{}'} />
              </Form.Item>}
            </Card>
          })}
          {!slotted && native && <Form.Item name="strategy" label="执行模式">
            <Select options={[{ value: 'EVALUATE', label: '正式评测' }, { value: 'OBSERVE_ONLY', label: '仅观察' }]} />
          </Form.Item>}
          {!slotted && showThreshold && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            <Form.Item name="measurement" hidden><Input /></Form.Item>
            {compatibility?.primary_measurement_title && <div style={{ gridColumn: '1 / -1' }}>
              <Text strong>{compatibility.primary_measurement_title}</Text>
              {compatibility.primary_measurement_formula && <div>{compatibility.primary_measurement_formula}</div>}
            </div>}
            <Form.Item name="operator" label="判定运算符" rules={[{ required: true }]}><Select options={[{ value: 'LT', label: '< 小于' }, { value: 'LTE', label: '≤ 小于等于' }, { value: 'GT', label: '> 大于' }, { value: 'GTE', label: '≥ 大于等于' }, { value: 'EQ', label: '= 等于' }, { value: 'NE', label: '≠ 不等于' }]} /></Form.Item>
            <Form.Item name="threshold" label="阈值" rules={[{ required: true }]}>{numericThreshold ? <InputNumber style={{ width: '100%' }} min={minimum} max={maximum} /> : <Input />}</Form.Item>
            <Form.Item name="severity" label="失败严重度" rules={[{ required: true }]}><Select options={(['INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const).map(value => ({ value, label: severityLabel(value) }))} /></Form.Item>
            {!native && <Form.Item name="on_missing" label="结果缺失策略" rules={[{ required: true }]}><Select options={[{ value: 'UNKNOWN', label: onMissingLabel('UNKNOWN') }, { value: 'SKIPPED', label: onMissingLabel('SKIPPED') }]} /></Form.Item>}
          </div>}
          {native && showThreshold && selectedStatistic && <Text type="secondary">{selectedStatistic.population} · {selectedStatistic.method}</Text>}
          <Title level={5}>Calculator 参数</Title>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            {!slotted && Object.entries(parameterProperties).map(([key, definition]) => {
              const name = ['parameters', key]
              if (definition.type === 'object' && definition.propertyNames?.enum) return <Collapse key={key} items={[{
                key, label: definition.title ?? key, forceRender: true,
                children: <Form.Item name={name} extra={definition.description}><ParameterOverrides schema={definition} /></Form.Item>,
              }]} />
              return <Form.Item key={key} name={name} label={definition.title ?? key} extra={definition.description}>
                {definition.type === 'integer' || definition.type === 'number' ? <NumericParameter schema={definition} /> : <ParameterInput schema={definition} />}
              </Form.Item>
            })}
          </div>
          {!slotted && <Button style={{ marginRight: 8 }} onClick={() => {
            form.setFieldValue('parameters', defaultParameters)
            form.resetFields(['operator', 'threshold'])
            setInputCheck(undefined)
          }}>恢复默认值</Button>}
          <Button type="primary" htmlType="submit" loading={publishing} disabled={versions.length === 0}>校验并发布契约</Button>
        </Form>}
      </Card>
    </Space>
  </div>
}
