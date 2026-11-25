import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Checkbox, Descriptions, Form, Input, InputNumber, Modal,
  Result, Select, Space, Spin, Statistic, Table, Tag, Typography,
} from 'antd'

import { listDatasets, listDatasetVersions, type DatasetVersion } from '../api/datasetVersions'
import {
  publishNativeQualityContract,
  publishQualityContract,
  publishOperatorContract,
  type PublishedQualityContract,
  type QualityContractPublishRequest,
  type RuleOperator,
  type Severity,
} from '../api/qualityContracts'
import type { ContractSeed } from './MetricRegistryPage'
import { createEvaluationRun, getEvaluationRun, getRunMetricResults, type EvaluationRun, type MetricResult } from '../api/evaluationRuns'
import { evaluationRunStatusLabel, isEvaluationRunTerminal } from '../utils/evaluationRunStatus'
import {
  datasetVersionStatusLabel,
  onMissingLabel,
  severityLabel,
  valueTypeLabel,
} from '../utils/displayLabels'

const { Title, Text } = Typography

interface Props {
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

export default function QualityContractBuilderPage({ seed, onBack }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [versions, setVersions] = useState<Array<DatasetVersion & { dataset_name: string }>>([])
  const [loadingVersions, setLoadingVersions] = useState(true)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [published, setPublished] = useState<PublishedQualityContract | null>(null)
  const [run, setRun] = useState<EvaluationRun | null>(null)
  const [evidencePreview, setEvidencePreview] = useState<string | null>(null)
  const [results, setResults] = useState<MetricResult[]>([])

  const defaultParameters = useMemo(() => Object.fromEntries(
    Object.entries(seed.calculator.parameter_schema.properties ?? {})
      .filter(([, definition]) => definition.default !== undefined)
      .map(([key, definition]) => [key, definition.default]),
  ), [seed.calculator.parameter_schema.properties])

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
  const statistics = seed.calculator.native_output?.statistics.filter(item => ['count', 'ratio', 'scalar'].includes(item.kind)) ?? []
  const strategy = Form.useWatch('strategy', form)
  const showThreshold = !native || strategy !== 'OBSERVE_ONLY'
  const measurement = Form.useWatch('measurement', form)
  const selectedMeasurement = availableMeasurements.find(item => item.key === measurement)
  const selectedStatistic = statistics.find(item => item.key === measurement)
  const numericThreshold = native || selectedMeasurement?.value_type === 'NUMBER'
  const minimum = native ? selectedStatistic?.minimum ?? undefined : selectedMeasurement?.minimum
  const maximum = native ? selectedStatistic?.maximum ?? undefined : selectedMeasurement?.maximum

  const submit = async (values: FormValues) => {
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
        {loadingVersions ? <Spin /> : <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={{ name: `${seed.metric.name}质量契约`, version_label: 'v1.0.0', strategy: 'EVALUATE', measurement: native ? (statistics.find(item => item.key === 'expired_evidence_rate') ?? statistics[0])?.key : availableMeasurements[0]?.key, operator: 'LTE', severity: 'ERROR', on_missing: 'UNKNOWN', parameters: defaultParameters }}
          onFinish={submit}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            <Form.Item name="name" label="契约名称" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="version_label" label="版本标签" rules={[{ required: true }]}><Input /></Form.Item>
          </div>
          <Form.Item name="dataset_version_id" label="目标数据集版本" rules={[{ required: true, message: '请选择已发布的数据集版本' }]}>
            <Select showSearch optionFilterProp="label" placeholder={versions.length ? '选择已发布版本' : '没有可用的已发布版本'} options={versions.map(version => ({ value: version.id, label: `${version.dataset_name} / ${version.version} (${version.id})` }))} />
          </Form.Item>
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
            <Form.Item name="measurement" label={native ? "原生统计字段" : "测量项"} rules={[{ required: true }]}><Select options={native ? statistics.map(item => ({ value: item.key, label: `${item.key} · ${item.unit}` })) : availableMeasurements.map(item => ({ value: item.key, label: `${item.key} · ${valueTypeLabel(item.value_type)}${item.unit ? ` · ${item.unit}` : ''}` }))} /></Form.Item>
            <Form.Item name="operator" label="判定运算符" rules={[{ required: true }]}><Select options={[{ value: 'LT', label: '< 小于' }, { value: 'LTE', label: '≤ 小于等于' }, { value: 'GT', label: '> 大于' }, { value: 'GTE', label: '≥ 大于等于' }, { value: 'EQ', label: '= 等于' }, { value: 'NE', label: '≠ 不等于' }]} /></Form.Item>
            <Form.Item name="threshold" label="阈值" rules={[{ required: true }]}>{numericThreshold ? <InputNumber style={{ width: '100%' }} min={minimum} max={maximum} /> : <Input />}</Form.Item>
            <Form.Item name="severity" label="失败严重度" rules={[{ required: true }]}><Select options={(['INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const).map(value => ({ value, label: severityLabel(value) }))} /></Form.Item>
            {!native && <Form.Item name="on_missing" label="结果缺失策略" rules={[{ required: true }]}><Select options={[{ value: 'UNKNOWN', label: onMissingLabel('UNKNOWN') }, { value: 'SKIPPED', label: onMissingLabel('SKIPPED') }]} /></Form.Item>}
          </div>}
          {native && showThreshold && selectedStatistic && <Text type="secondary">{selectedStatistic.population} · {selectedStatistic.method}</Text>}
          <Title level={5}>Calculator 参数</Title>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            {!slotted && Object.entries(parameterProperties).map(([key, definition]) => <Form.Item key={key} name={['parameters', key]} label={key} rules={[{ required: false }]}>
              {definition.type === 'integer' || definition.type === 'number' ? <InputNumber style={{ width: '100%' }} min={definition.minimum} max={definition.maximum} /> : <Input />}
            </Form.Item>)}
          </div>
          <Button type="primary" htmlType="submit" loading={publishing} disabled={versions.length === 0}>校验并发布契约</Button>
        </Form>}
      </Card>
    </Space>
  </div>
}
