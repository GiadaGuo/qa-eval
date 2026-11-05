import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Form, Input, InputNumber,
  Result, Select, Space, Spin, Statistic, Table, Tag, Typography,
} from 'antd'

import { listDatasets, listDatasetVersions, type DatasetVersion } from '../api/datasetVersions'
import {
  publishQualityContract,
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
  operator: RuleOperator
  threshold: number | string
  severity: Severity
  on_missing: 'UNKNOWN' | 'SKIPPED'
  parameters: Record<string, unknown>
}

export default function QualityContractBuilderPage({ seed, onBack }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [versions, setVersions] = useState<Array<DatasetVersion & { dataset_name: string }>>([])
  const [loadingVersions, setLoadingVersions] = useState(true)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [published, setPublished] = useState<PublishedQualityContract | null>(null)
  const [run, setRun] = useState<EvaluationRun | null>(null)
  const [results, setResults] = useState<MetricResult[]>([])

  const defaultParameters = useMemo(() => Object.fromEntries(
    Object.entries(seed.calculator.parameter_schema.properties ?? {})
      .filter(([, definition]) => definition.default !== undefined)
      .map(([key, definition]) => [key, definition.default]),
  ), [seed.calculator.parameter_schema.properties])

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

  const measurement = Form.useWatch('measurement', form)
  const selectedMeasurement = seed.metric.measurements.find(item => item.key === measurement)
  const numericThreshold = selectedMeasurement?.value_type === 'NUMBER'

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
      setPublished(await publishQualityContract(payload))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '发布质量契约失败')
    } finally {
      setPublishing(false)
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
        {results.map(result => <Table key={result.metric_result_id} style={{ marginTop: 16 }} rowKey="key" pagination={false} dataSource={result.observations} columns={[{ title: '测量项', dataIndex: 'key' }, { title: '值', dataIndex: 'value', render: value => String(value) }, { title: '单位', dataIndex: 'unit', render: value => value ?? '—' }]} />)}
      </Card>}
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
          initialValues={{ name: `${seed.metric.name}质量契约`, version_label: 'v1.0.0', measurement: seed.metric.measurements[0]?.key, operator: 'LTE', severity: 'ERROR', on_missing: 'UNKNOWN', parameters: defaultParameters }}
          onFinish={submit}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            <Form.Item name="name" label="契约名称" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="version_label" label="版本标签" rules={[{ required: true }]}><Input /></Form.Item>
          </div>
          <Form.Item name="dataset_version_id" label="目标数据集版本" rules={[{ required: true, message: '请选择已发布的数据集版本' }]}>
            <Select showSearch optionFilterProp="label" placeholder={versions.length ? '选择已发布版本' : '没有可用的已发布版本'} options={versions.map(version => ({ value: version.id, label: `${version.dataset_name} / ${version.version} (${version.id})` }))} />
          </Form.Item>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            <Form.Item name="measurement" label="测量项" rules={[{ required: true }]}><Select options={seed.metric.measurements.map(item => ({ value: item.key, label: `${item.key} · ${valueTypeLabel(item.value_type)}${item.unit ? ` · ${item.unit}` : ''}` }))} /></Form.Item>
            <Form.Item name="operator" label="判定运算符" rules={[{ required: true }]}><Select options={[{ value: 'LT', label: '< 小于' }, { value: 'LTE', label: '≤ 小于等于' }, { value: 'GT', label: '> 大于' }, { value: 'GTE', label: '≥ 大于等于' }, { value: 'EQ', label: '= 等于' }, { value: 'NE', label: '≠ 不等于' }]} /></Form.Item>
            <Form.Item name="threshold" label="阈值" rules={[{ required: true }]}>{numericThreshold ? <InputNumber style={{ width: '100%' }} min={selectedMeasurement?.minimum} max={selectedMeasurement?.maximum} /> : <Input />}</Form.Item>
            <Form.Item name="severity" label="失败严重度" rules={[{ required: true }]}><Select options={(['INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const).map(value => ({ value, label: severityLabel(value) }))} /></Form.Item>
            <Form.Item name="on_missing" label="结果缺失策略" rules={[{ required: true }]}><Select options={[{ value: 'UNKNOWN', label: onMissingLabel('UNKNOWN') }, { value: 'SKIPPED', label: onMissingLabel('SKIPPED') }]} /></Form.Item>
          </div>
          <Title level={5}>Calculator 参数</Title>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            {Object.entries(parameterProperties).map(([key, definition]) => <Form.Item key={key} name={['parameters', key]} label={key} rules={[{ required: false }]}>
              {definition.type === 'integer' || definition.type === 'number' ? <InputNumber style={{ width: '100%' }} min={definition.minimum} max={definition.maximum} /> : <Input />}
            </Form.Item>)}
          </div>
          <Button type="primary" htmlType="submit" loading={publishing} disabled={versions.length === 0}>校验并发布契约</Button>
        </Form>}
      </Card>
    </Space>
  </div>
}
