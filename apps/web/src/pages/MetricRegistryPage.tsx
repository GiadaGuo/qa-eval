import { useEffect, useMemo, useState } from 'react'
import { ApiOutlined, SearchOutlined, SyncOutlined, UploadOutlined } from '@ant-design/icons'
import {
  Alert, Button, Card, Descriptions, Empty, Input, Modal, Skeleton,
  Space, Table, Tag, Typography, Upload,
} from 'antd'
import type { TableColumnsType } from 'antd'

import {
  fetchBindings, fetchCalculators, fetchMetrics, syncRegistry, testDocumentCalculator,
  type CalculatorDescriptor,
  type MetricCalculatorBinding, type MetricDefinition,
} from '../api/registry'
import { formatModalityList } from '../utils/displayLabels'
import { compareMetricVersions, isVisibleCatalogMetric, latestMetricVersions } from '../utils/latestMetricVersions'

const { Title, Text, Paragraph } = Typography

export interface ContractSeed {
  metric: MetricDefinition
  calculator: CalculatorDescriptor
  binding: MetricCalculatorBinding
  operators?: Array<{ calculator: CalculatorDescriptor; binding: MetricCalculatorBinding }>
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

type DocumentCheckItem = {
  item_code: string
  item_name: string
  satisfied_standard: string
  counterexamples?: string[]
  notes?: string | null
  applicable?: boolean
}

type DocumentCheckConfiguration = { version: string; items: DocumentCheckItem[] }

function DocumentCalculatorTest({ calculator }: { calculator: CalculatorDescriptor }) {
  const [file, setFile] = useState<File>()
  const configuration = calculator.parameter_schema.properties?.configuration?.default as DocumentCheckConfiguration | undefined
  const [result, setResult] = useState('')
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)

  const run = async () => {
    if (!file) return
    setRunning(true)
    setError('')
    setResult('')
    try {
      setResult(JSON.stringify(await testDocumentCalculator(calculator, file, JSON.stringify({ configuration })), null, 2))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '算子测试失败')
    } finally {
      setRunning(false)
    }
  }

  return <Space orientation="vertical" size="middle" style={{ width: '100%', marginTop: 16 }}>
    <Space wrap>
      <Text strong>说明文档</Text>
      <Upload accept=".md,.pdf,.docx" maxCount={1} disabled={running} fileList={file ? [{ uid: 'document', name: file.name }] : []}
        beforeUpload={document => { setFile(document); return false }} onRemove={() => setFile(undefined)}>
        <Button icon={<UploadOutlined />}>选择文件</Button>
      </Upload>
    </Space>
    <Space wrap>
      <Text strong>本次测试规则</Text>
      {configuration && <Text type="secondary">规则版本 {configuration.version}</Text>}
    </Space>
    {configuration && <Table size="small" bordered pagination={false} scroll={{ x: 720, y: 380 }}
      dataSource={configuration.items} rowKey="item_code"
      columns={[
        { title: '检查项', width: 170, render: (_, item) => <Space orientation="vertical" size={2}>
          <Text type="secondary">{item.item_code}</Text>
          <Text strong>{item.item_name}</Text>
        </Space> },
        { title: '达标标准', dataIndex: 'satisfied_standard' },
        { title: '反例与备注', width: '34%', render: (_, item) => <Space orientation="vertical" size={4}>
          {item.counterexamples?.map((example, index) => <Text key={index}>{example}</Text>)}
          {item.notes && <Text type="secondary">备注 · {item.notes}</Text>}
          {!item.counterexamples?.length && !item.notes && <Text type="secondary">—</Text>}
        </Space> },
        { title: '适用', width: 84, render: (_, item) => item.applicable === false ? '不适用' : '适用' },
      ]} />}
    <div style={{ textAlign: 'right' }}>
      <Button type="primary" disabled={!file} loading={running} onClick={run}>运行单算子测试</Button>
    </div>
    {error && <Alert type="error" title={error} />}
    {result && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 400, overflow: 'auto' }}>{result}</pre>}
  </Space>
}

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
  const calculatorsByKey = useMemo(
    () => new Map(calculators.map(calculator => [calculatorKey(calculator), calculator])),
    [calculators],
  )
  const latestMetrics = useMemo(() => latestMetricVersions(metrics.filter(metric =>
    isVisibleCatalogMetric(metric.metric_key) && metric.metric_key !== 'BG01_CARD_CONFORMANCE'
  )), [metrics])
  const filteredMetrics = useMemo(() => latestMetrics.filter(metric => !normalizedQuery
    || metric.metric_key.toLocaleLowerCase().includes(normalizedQuery)
    || metric.name.toLocaleLowerCase().includes(normalizedQuery)), [latestMetrics, normalizedQuery])
  const selectedBindings = selectedMetric ? bindings.filter(binding =>
    binding.metric_key === selectedMetric.metric_key && binding.metric_version === selectedMetric.version,
  ).sort((left, right) => right.priority - left.priority || compareMetricVersions(right.calculator_version, left.calculator_version) || right.calculator_key.localeCompare(left.calculator_key)) : []
  const calculatorRows: CalculatorImplementationRow[] = selectedBindings.map(binding => {
    const calculator = calculatorsByKey.get(`${binding.calculator_key}@${binding.calculator_version}`)
    const calculatorAvailable = isExecutable(calculator)
    const available = binding.enabled && calculatorAvailable
    const availabilityLabel = !binding.enabled
      ? 'Binding 已停用'
      : !calculator
        ? 'Descriptor 不存在'
        : calculatorAvailable
          ? binding.projections ? '原生观察' : '实现可用'
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

  const calculatorColumns: TableColumnsType<CalculatorImplementationRow> = [
    {
      title: '算子名称',
      key: 'name',
      render: (_, row) => <Space direction="vertical" size={0}>
        <Text strong>{row.calculator?.name ?? row.binding.calculator_key}</Text>
        <Text type="secondary" code>{row.binding.calculator_key}</Text>
      </Space>,
    },
    { title: '槽位', key: 'slot', render: (_, row) => row.binding.compatibility?.operator_slot ?? '—' },
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
      {syncNotice && <Alert type="success" showIcon closable title={syncNotice} onClose={() => setSyncNotice(null)} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Input allowClear size="large" prefix={<SearchOutlined />} placeholder="按指标编码或名称搜索，例如 0208、内容干净性" value={query} onChange={event => setQuery(event.target.value)} style={{ width: '100%', maxWidth: 620, flex: '1 1 320px' }} />
        <Button type="primary" size="large" icon={<SyncOutlined spin={syncing} />} loading={syncing} disabled={loading} onClick={handleSync}>Registry 同步</Button>
      </div>
      {error && <Alert type="error" showIcon title="Registry 加载失败" description={error} action={<Button onClick={loadRegistry}>重新加载</Button>} />}
      {loading ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>{[1, 2, 3].map(item => <Card key={item}><Skeleton active /></Card>)}</div>
        : filteredMetrics.length === 0 ? <Empty description={normalizedQuery ? `未找到匹配“${query.trim()}”的指标` : '尚未注册指标'} />
          : <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
            {filteredMetrics.map(metric => {
              const metricBindings = bindings.filter(binding => binding.metric_key === metric.metric_key && binding.metric_version === metric.version)
              const enabledCount = metricBindings.filter(binding => binding.enabled && isExecutable(calculatorsByKey.get(`${binding.calculator_key}@${binding.calculator_version}`))).length
              return <Card key={`${metric.metric_key}@${metric.version}`} hoverable onClick={() => setSelectedMetric(metric)}
                style={{ display: 'flex', flexDirection: 'column' }}
                styles={{ body: { display: 'flex', flexDirection: 'column', flex: 1 } }}>
                <Space wrap style={{ marginBottom: 14 }}><Tag color="blue">{metric.metric_key}</Tag><Tag>{dimensionLabels[metric.dimension] ?? metric.dimension}</Tag><Tag>v{metric.version}</Tag></Space>
                <Title level={4} style={{ marginTop: 0 }}>{metric.name}</Title>
                <Paragraph type="secondary" ellipsis={{ rows: 3 }}>{metric.definition.requirement}</Paragraph>
                <Space wrap style={{ marginTop: 'auto' }}><Tag icon={<ApiOutlined />} color={enabledCount ? 'success' : 'default'}>{enabledCount} 个可用 Calculator</Tag></Space>
              </Card>
            })}
          </div>}
    </Space>

    <Modal open={selectedMetric !== null} width={1040} footer={null} onCancel={closeMetric} title={selectedMetric ? `${selectedMetric.metric_key} · ${selectedMetric.name}` : '指标详情'} destroyOnHidden>
      {selectedMetric && <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="指标版本">{selectedMetric.version}</Descriptions.Item><Descriptions.Item label="质量维度">{dimensionLabels[selectedMetric.dimension] ?? selectedMetric.dimension}</Descriptions.Item>
          <Descriptions.Item label="适用模态">{formatModalityList(selectedMetric.applicability.modalities) || '—'}</Descriptions.Item><Descriptions.Item label="必需内容角色">{selectedMetric.applicability.required_roles.join('、') || '—'}</Descriptions.Item>
          <Descriptions.Item label="指标定义" span={2}>{selectedMetric.definition.requirement}</Descriptions.Item>
          <Descriptions.Item label="语义限制" span={2}>{selectedMetric.definition.limitations.length ? selectedMetric.definition.limitations.map(item => <div key={item}>• {item}</div>) : '—'}</Descriptions.Item>
        </Descriptions>
        <div><Title level={4} style={{ marginTop: 0 }}>Calculator 实现</Title>
          {onBuildContract && calculatorRows.some(row => row.available && row.binding.compatibility?.operator_slot) && <Button
            onClick={() => {
              const operators = calculatorRows.flatMap(({ calculator, binding, available }) =>
                available && calculator && binding.compatibility?.operator_slot ? [{ calculator, binding }] : [],
              )
              if (operators.length) onBuildContract?.({ metric: selectedMetric, ...operators[0], operators })
            }}
          >选择算子并构建契约</Button>}
          {calculatorRows.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该指标尚未绑定 Calculator" />
            : <Table
              size="small"
              rowKey="key"
              columns={calculatorColumns}
              dataSource={calculatorRows}
              pagination={{ pageSize: 5, showSizeChanger: false }}
              scroll={{ x: 720, y: 380 }}
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
          {selectedCalculator.input_contract.logical?.grain !== 'DOCUMENT' && <Descriptions.Item label="参数" span={2}><Space wrap>{Object.entries(selectedCalculator.parameter_schema.properties ?? {}).map(([key, value]) => <Tag key={key}>{key}{value.default === undefined ? '' : ` = ${String(value.default)}`}</Tag>)}</Space></Descriptions.Item>}
        </Descriptions>
        {selectedCalculator.input_contract.logical?.grain === 'DOCUMENT'
          ? selectedImplementationAvailable && <DocumentCalculatorTest key={calculatorKey(selectedCalculator)} calculator={selectedCalculator} />
          : <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 16 }}>{selectedDisabledReason && <Text type="secondary">{selectedDisabledReason}</Text>}<Button type="primary" disabled={(!!selectedBinding.projections && !selectedCalculator.native_output) || !selectedImplementationAvailable || !onBuildContract || !selectedMetric} onClick={() => selectedMetric && onBuildContract?.({ metric: selectedMetric, calculator: selectedCalculator, binding: selectedBinding, operators: selectedBinding.compatibility?.operator_slot ? [{ calculator: selectedCalculator, binding: selectedBinding }] : undefined })}>构建质量契约</Button></div>}
      </Card> : <Alert type="error" showIcon title="绑定引用的 Calculator Descriptor 不存在" />)}
    </Modal>
  </div>
}
