import { useEffect, useMemo, useState } from 'react'
import { ApiOutlined, DatabaseOutlined, SearchOutlined, SyncOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Empty, Input, Modal, Skeleton,
  Space, Table, Tag, Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'

import {
  fetchBindings, fetchCalculators, fetchMetrics, syncRegistry,
  type CalculatorDescriptor, type Measurement,
  type MetricCalculatorBinding, type MetricDefinition,
} from '../api/registry'
import { formatModalityList } from '../utils/displayLabels'

const { Title, Text, Paragraph } = Typography

export interface ContractSeed {
  metric: MetricDefinition
  calculator: CalculatorDescriptor
  binding: MetricCalculatorBinding
}

interface MetricRegistryPageProps {
  onBuildContract?: (seed: ContractSeed) => void
}

interface CalculatorImplementationRow {
  key: string
  binding: MetricCalculatorBinding
  calculator?: CalculatorDescriptor
  available: boolean
  availabilityLabel: string
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
  const [selectedBinding, setSelectedBinding] = useState<MetricCalculatorBinding | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncNotice, setSyncNotice] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [loading, setLoading] = useState(true)

  const loadRegistry = () => {
    setLoading(true)
    setError(null)
    return Promise.all([fetchMetrics(), fetchCalculators(), fetchBindings()])
      .then(([metricItems, calculatorItems, bindingItems]) => {
        setMetrics(metricItems)
        setCalculators(calculatorItems)
        setBindings(bindingItems)
        return true
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : '未知错误')
        return false
      })
      .finally(() => setLoading(false))
  }

  const handleSync = async () => {
    setSyncing(true)
    setError(null)
    setSyncNotice(null)
    try {
      const result = await syncRegistry()
      const reloaded = await loadRegistry()
      if (reloaded) {
        setSyncNotice(`Registry 同步完成：${result.metrics} 个指标、${result.calculators} 个 Calculator、${result.bindings} 个 Binding`)
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Registry 同步失败')
    } finally {
      setSyncing(false)
    }
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
  const calculatorRows: CalculatorImplementationRow[] = selectedBindings.map(binding => {
    const calculator = calculatorsByKey.get(`${binding.calculator_key}@${binding.calculator_version}`)
    const calculatorAvailable = isExecutable(calculator)
    const available = binding.enabled && calculatorAvailable
    const availabilityLabel = !binding.enabled
      ? 'Binding 已停用'
      : !calculator
        ? 'Descriptor 不存在'
        : calculatorAvailable
          ? '实现可用'
          : (calculator.availability.reason ?? 'Calculator 不可用')
    return {
      key: `${binding.metric_key}@${binding.metric_version}@${binding.calculator_key}@${binding.calculator_version}`,
      binding,
      calculator,
      available,
      availabilityLabel,
    }
  })
  const selectedCalculator = selectedBinding
    ? calculatorsByKey.get(`${selectedBinding.calculator_key}@${selectedBinding.calculator_version}`)
    : undefined
  const selectedCalculatorAvailable = isExecutable(selectedCalculator)
  const selectedImplementationAvailable = Boolean(selectedBinding?.enabled && selectedCalculatorAvailable)
  const selectedDisabledReason = selectedBinding && !selectedBinding.enabled
    ? 'Binding 已停用'
    : !selectedCalculator
      ? 'Calculator Descriptor 不存在'
      : !selectedCalculatorAvailable
        ? (selectedCalculator.availability.reason ?? 'Calculator 不可用')
        : !onBuildContract
          ? '质量契约服务将在闭环 3B 接入'
          : null

  const measurementColumns: TableColumnsType<Measurement> = [
    { title: '测量项', dataIndex: 'key', key: 'key' },
    { title: '类型', dataIndex: 'value_type', key: 'value_type', width: 100 },
    { title: '单位', dataIndex: 'unit', key: 'unit', width: 100, render: value => value ?? '—' },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 170 },
    { title: '解释', dataIndex: 'interpretation', key: 'interpretation', render: value => value ?? '—' },
  ]
  const calculatorColumns: TableColumnsType<CalculatorImplementationRow> = [
    {
      title: '算子名称',
      key: 'name',
      render: (_, row) => <Space direction="vertical" size={0}>
        <Text strong>{row.calculator?.name ?? row.binding.calculator_key}</Text>
        <Text type="secondary" code>{row.binding.calculator_key}</Text>
      </Space>,
    },
    { title: '优先级', dataIndex: ['binding', 'priority'], key: 'priority', width: 110 },
    {
      title: '可用性',
      key: 'availability',
      width: 180,
      render: (_, row) => <Tag color={row.available ? 'success' : 'default'}>{row.availabilityLabel}</Tag>,
    },
    { title: '版本', dataIndex: ['binding', 'calculator_version'], key: 'version', width: 120, render: value => `v${String(value)}` },
  ]

  const closeMetric = () => {
    setSelectedBinding(null)
    setSelectedMetric(null)
  }

  return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div><Title level={2} style={{ marginBottom: 4 }}>指标管理</Title><Text type="secondary">浏览指南指标及其注册实现。Metric Definition、Calculator 和 Binding 保持独立。</Text></div>
      {syncNotice && <Alert type="success" showIcon closable title={syncNotice} onClose={() => setSyncNotice(null)} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Input allowClear size="large" prefix={<SearchOutlined />} placeholder="按指标编码或名称搜索，例如 02080103、重复内容程度" value={query} onChange={event => setQuery(event.target.value)} style={{ width: '100%', maxWidth: 620, flex: '1 1 320px' }} />
        <Button type="primary" size="large" icon={<SyncOutlined spin={syncing} />} loading={syncing} disabled={loading} onClick={handleSync}>Registry 同步</Button>
      </div>
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
                <Space wrap style={{ marginTop: 'auto' }}><Tag icon={<DatabaseOutlined />}>{metric.measurements.length} 个测量项</Tag><Tag icon={<ApiOutlined />} color={enabledCount ? 'success' : 'default'}>{enabledCount} 个可用 Calculator</Tag></Space>
              </Card>
            })}
          </div>}
    </Space>

    <Modal open={selectedMetric !== null} width={1040} footer={null} onCancel={closeMetric} title={selectedMetric ? `${selectedMetric.metric_key} · ${selectedMetric.name}` : '指标详情'} destroyOnHidden>
      {selectedMetric && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="指标版本">{selectedMetric.version}</Descriptions.Item><Descriptions.Item label="质量维度">{dimensionLabels[selectedMetric.dimension] ?? selectedMetric.dimension}</Descriptions.Item>
          <Descriptions.Item label="适用模态">{formatModalityList(selectedMetric.applicability.modalities) || '—'}</Descriptions.Item><Descriptions.Item label="必需内容角色">{selectedMetric.applicability.required_roles.join('、') || '—'}</Descriptions.Item>
          <Descriptions.Item label="指标定义" span={2}>{selectedMetric.definition.requirement}</Descriptions.Item>
          <Descriptions.Item label="语义限制" span={2}>{selectedMetric.definition.limitations.length ? selectedMetric.definition.limitations.map(item => <div key={item}>• {item}</div>) : '—'}</Descriptions.Item>
        </Descriptions>
        <Card size="small" title="测量项"><Table size="small" pagination={false} rowKey="key" columns={measurementColumns} dataSource={selectedMetric.measurements} scroll={{ x: 760 }} /></Card>
        <div><Title level={4}>Calculator 实现</Title>
          {calculatorRows.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该指标尚未绑定 Calculator" />
            : <Table
              size="small"
              rowKey="key"
              columns={calculatorColumns}
              dataSource={calculatorRows}
              pagination={{ pageSize: 5, showSizeChanger: false }}
              scroll={{ x: 720 }}
              onRow={row => ({
                onClick: () => setSelectedBinding(row.binding),
                onKeyDown: event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setSelectedBinding(row.binding)
                  }
                },
                role: 'button',
                tabIndex: 0,
                style: { cursor: 'pointer' },
              })}
            />}
        </div>
      </Space>}
    </Modal>

    <Modal open={selectedBinding !== null} width={900} footer={null} onCancel={() => setSelectedBinding(null)} title={selectedCalculator?.name ?? selectedBinding?.calculator_key ?? 'Calculator 详情'} destroyOnHidden>
      {selectedBinding && (selectedCalculator ? <Card size="small" extra={<Space><Tag>优先级 {selectedBinding.priority}</Tag><Tag color={selectedImplementationAvailable ? 'success' : 'default'}>{selectedImplementationAvailable ? '实现可用' : '不可执行'}</Tag></Space>}>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="版本">{selectedCalculator.version}</Descriptions.Item><Descriptions.Item label="确定性">{selectedCalculator.deterministic ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="输入角色">{selectedCalculator.input_contract.role_selectors.join(', ') || '—'}</Descriptions.Item><Descriptions.Item label="生成证据">{selectedCalculator.capabilities.produces_evidence ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="Entrypoint" span={2}>{selectedCalculator.entrypoint ? <Text code>{selectedCalculator.entrypoint}</Text> : '—（待实现，暂无执行入口）'}</Descriptions.Item><Descriptions.Item label="输出 Schema" span={2}><Text code>{selectedCalculator.output_schema_ref}</Text></Descriptions.Item>
          <Descriptions.Item label="参数" span={2}><Space wrap>{Object.entries(selectedCalculator.parameter_schema.properties ?? {}).map(([key, value]) => <Tag key={key}>{key}{value.default === undefined ? '' : ` = ${String(value.default)}`}</Tag>)}</Space></Descriptions.Item>
        </Descriptions>
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 16 }}>{selectedDisabledReason && <Text type="secondary">{selectedDisabledReason}</Text>}<Button type="primary" disabled={!selectedImplementationAvailable || !onBuildContract || !selectedMetric} onClick={() => selectedMetric && onBuildContract?.({ metric: selectedMetric, calculator: selectedCalculator, binding: selectedBinding })}>构建质量契约</Button></div>
      </Card> : <Alert type="error" showIcon title="绑定引用的 Calculator Descriptor 不存在" />)}
    </Modal>
  </div>
}
