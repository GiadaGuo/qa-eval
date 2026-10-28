import { useEffect, useMemo, useState } from 'react'
import { ApiOutlined, DatabaseOutlined, SearchOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Empty, Input, Modal, Skeleton,
  Space, Table, Tag, Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'

import {
  fetchBindings, fetchCalculators, fetchMetrics,
  type CalculatorDescriptor, type Measurement,
  type MetricCalculatorBinding, type MetricDefinition,
} from '../api/registry'

const { Title, Text, Paragraph } = Typography

export interface ContractSeed {
  metric: MetricDefinition
  calculator: CalculatorDescriptor
  binding: MetricCalculatorBinding
}

interface MetricRegistryPageProps {
  onBuildContract?: (seed: ContractSeed) => void
}

const dimensionLabels: Record<string, string> = {
  DOCUMENTATION: '说明文档质量', DATA_QUALITY: '数据质量', MODEL_APPLICATION: '模型应用质量',
}

const calculatorKey = (calculator: CalculatorDescriptor) => `${calculator.calculator_key}@${calculator.version}`
const isExecutable = (calculator: CalculatorDescriptor | undefined) => calculator !== undefined && calculator.availability.status === 'AVAILABLE'

export default function MetricRegistryPage({ onBuildContract }: MetricRegistryPageProps) {
  const [metrics, setMetrics] = useState<MetricDefinition[]>([])
  const [calculators, setCalculators] = useState<CalculatorDescriptor[]>([])
  const [bindings, setBindings] = useState<MetricCalculatorBinding[]>([])
  const [query, setQuery] = useState('')
  const [selectedMetric, setSelectedMetric] = useState<MetricDefinition | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadRegistry = () => {
    setLoading(true)
    setError(null)
    Promise.all([fetchMetrics(), fetchCalculators(), fetchBindings()])
      .then(([metricItems, calculatorItems, bindingItems]) => {
        setMetrics(metricItems)
        setCalculators(calculatorItems)
        setBindings(bindingItems)
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '未知错误'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchMetrics(), fetchCalculators(), fetchBindings()])
      .then(([metricItems, calculatorItems, bindingItems]) => {
        if (cancelled) return
        setMetrics(metricItems)
        setCalculators(calculatorItems)
        setBindings(bindingItems)
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '未知错误')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredMetrics = useMemo(() => metrics.filter(metric => !normalizedQuery
    || metric.metric_key.toLocaleLowerCase().includes(normalizedQuery)
    || metric.name.toLocaleLowerCase().includes(normalizedQuery)), [metrics, normalizedQuery])
  const calculatorsByKey = useMemo(
    () => new Map(calculators.map(calculator => [calculatorKey(calculator), calculator])),
    [calculators],
  )
  const selectedBindings = selectedMetric ? bindings.filter(binding =>
    binding.metric_key === selectedMetric.metric_key && binding.metric_version === selectedMetric.version,
  ).sort((left, right) => right.priority - left.priority) : []

  const measurementColumns: TableColumnsType<Measurement> = [
    { title: 'Measurement', dataIndex: 'key', key: 'key' },
    { title: '类型', dataIndex: 'value_type', key: 'value_type', width: 100 },
    { title: '单位', dataIndex: 'unit', key: 'unit', width: 100, render: value => value ?? '—' },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 170 },
    { title: '解释', dataIndex: 'interpretation', key: 'interpretation', render: value => value ?? '—' },
  ]

  return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div><Title level={2} style={{ marginBottom: 4 }}>指标管理</Title><Text type="secondary">浏览指南指标及其注册实现。Metric Definition、Calculator 和 Binding 保持独立。</Text></div>
      <Input allowClear size="large" prefix={<SearchOutlined />} placeholder="按指标编码或名称搜索，例如 02080103、重复内容程度" value={query} onChange={event => setQuery(event.target.value)} style={{ maxWidth: 620 }} />
      {error && <Alert type="error" showIcon title="Registry 加载失败" description={error} action={<Button onClick={loadRegistry}>重新加载</Button>} />}
      {loading ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>{[1, 2, 3].map(item => <Card key={item}><Skeleton active /></Card>)}</div>
        : filteredMetrics.length === 0 ? <Empty description={normalizedQuery ? `未找到匹配“${query.trim()}”的指标` : '尚未注册指标'} />
          : <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
            {filteredMetrics.map(metric => {
              const metricBindings = bindings.filter(binding => binding.metric_key === metric.metric_key && binding.metric_version === metric.version)
              const enabledCount = metricBindings.filter(binding => binding.enabled && isExecutable(calculatorsByKey.get(`${binding.calculator_key}@${binding.calculator_version}`))).length
              return <Card key={`${metric.metric_key}@${metric.version}`} hoverable onClick={() => setSelectedMetric(metric)} styles={{ body: { minHeight: 230, display: 'flex', flexDirection: 'column' } }}>
                <Space wrap style={{ marginBottom: 14 }}><Tag color="blue">{metric.metric_key}</Tag><Tag>{dimensionLabels[metric.dimension] ?? metric.dimension}</Tag><Tag>v{metric.version}</Tag></Space>
                <Title level={4} style={{ marginTop: 0 }}>{metric.name}</Title>
                <Paragraph type="secondary" ellipsis={{ rows: 3 }}>{metric.definition.requirement}</Paragraph>
                <Space wrap style={{ marginTop: 'auto' }}><Tag icon={<DatabaseOutlined />}>{metric.measurements.length} measurements</Tag><Tag icon={<ApiOutlined />} color={enabledCount ? 'success' : 'default'}>{enabledCount} 个可用 Calculator</Tag></Space>
              </Card>
            })}
          </div>}
    </Space>

    <Modal open={selectedMetric !== null} width={1040} footer={null} onCancel={() => setSelectedMetric(null)} title={selectedMetric ? `${selectedMetric.metric_key} · ${selectedMetric.name}` : '指标详情'} destroyOnHidden>
      {selectedMetric && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="指标版本">{selectedMetric.version}</Descriptions.Item><Descriptions.Item label="质量维度">{dimensionLabels[selectedMetric.dimension] ?? selectedMetric.dimension}</Descriptions.Item>
          <Descriptions.Item label="适用模态">{selectedMetric.applicability.modalities.join(', ') || '—'}</Descriptions.Item><Descriptions.Item label="必需内容角色">{selectedMetric.applicability.required_roles.join(', ') || '—'}</Descriptions.Item>
          <Descriptions.Item label="指标定义" span={2}>{selectedMetric.definition.requirement}</Descriptions.Item>
          <Descriptions.Item label="语义限制" span={2}>{selectedMetric.definition.limitations.length ? selectedMetric.definition.limitations.map(item => <div key={item}>• {item}</div>) : '—'}</Descriptions.Item>
        </Descriptions>
        <Card size="small" title="Measurements"><Table size="small" pagination={false} rowKey="key" columns={measurementColumns} dataSource={selectedMetric.measurements} scroll={{ x: 760 }} /></Card>
        <div><Title level={4}>Calculator 实现</Title>
          {selectedBindings.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该指标尚未绑定 Calculator" />
            : <Space direction="vertical" size="middle" style={{ width: '100%' }}>{selectedBindings.map(binding => {
              const calculator = calculatorsByKey.get(`${binding.calculator_key}@${binding.calculator_version}`)
              const calculatorAvailable = isExecutable(calculator)
              const implementationAvailable = binding.enabled && calculatorAvailable
              const disabledReason = !binding.enabled ? 'Binding 已停用' : !calculator ? 'Calculator Descriptor 不存在' : !calculatorAvailable ? (calculator.availability.reason ?? 'Calculator 不可用') : !onBuildContract ? '质量契约服务将在闭环 3B 接入' : null
              return <Card key={`${binding.metric_key}@${binding.metric_version}@${binding.calculator_key}@${binding.calculator_version}`} size="small" title={calculator?.name ?? binding.calculator_key} extra={<Space><Tag>优先级 {binding.priority}</Tag><Tag color={implementationAvailable ? 'success' : 'default'}>{implementationAvailable ? '实现可用' : calculator && !calculatorAvailable ? '待实现' : '不可执行'}</Tag></Space>}>
                {calculator ? <><Descriptions size="small" column={{ xs: 1, sm: 2 }}>
                  <Descriptions.Item label="版本">{calculator.version}</Descriptions.Item><Descriptions.Item label="确定性">{calculator.deterministic ? '是' : '否'}</Descriptions.Item>
                  <Descriptions.Item label="输入角色">{calculator.input_contract.role_selectors.join(', ')}</Descriptions.Item><Descriptions.Item label="生成证据">{calculator.capabilities.produces_evidence ? '是' : '否'}</Descriptions.Item>
                  <Descriptions.Item label="Entrypoint" span={2}>{calculator.entrypoint ? <Text code>{calculator.entrypoint}</Text> : '—（待实现，暂无执行入口）'}</Descriptions.Item><Descriptions.Item label="输出 Schema" span={2}><Text code>{calculator.output_schema_ref}</Text></Descriptions.Item>
                  <Descriptions.Item label="参数" span={2}><Space wrap>{Object.entries(calculator.parameter_schema.properties ?? {}).map(([key, value]) => <Tag key={key}>{key}{value.default === undefined ? '' : ` = ${String(value.default)}`}</Tag>)}</Space></Descriptions.Item>
                </Descriptions><div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 16 }}>{disabledReason && <Text type="secondary">{disabledReason}</Text>}<Button type="primary" disabled={!implementationAvailable || !onBuildContract} onClick={() => onBuildContract?.({ metric: selectedMetric, calculator, binding })}>构建质量契约</Button></div></>
                  : <Alert type="error" showIcon title="绑定引用的 Calculator Descriptor 不存在" />}
              </Card>
            })}</Space>}
        </div>
      </Space>}
    </Modal>
  </div>
}
