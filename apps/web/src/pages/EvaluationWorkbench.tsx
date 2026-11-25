import { useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Select, Space, Tabs, Tag } from 'antd'
import { AuditOutlined, BarChartOutlined, ControlOutlined, ProfileOutlined, ReloadOutlined } from '@ant-design/icons'
import { listDatasets, listDatasetVersions, type Dataset, type DatasetVersion } from '../api/datasetVersions'
import { fetchMetrics } from '../api/registry'
import AgentEvaluationWorkspace from './AgentEvaluationWorkspace'
import EvaluationResultsPage from './EvaluationResultsPage'
import MetricRegistryPage, { type ContractSeed } from './MetricRegistryPage'
import QualityContractBuilderPage from './QualityContractBuilderPage'

type Props = {
  initialDataset: Dataset | null
  initialVersion: DatasetVersion | null
  onOpenDatasets: (dataset?: Dataset) => void
  onBackToVersion: () => void
}

export default function EvaluationWorkbench({ initialDataset, initialVersion, onOpenDatasets, onBackToVersion }: Props) {
  const [tab, setTab] = useState('dataset')
  const [datasets, setDatasets] = useState<Dataset[]>(initialDataset ? [initialDataset] : [])
  const [datasetId, setDatasetId] = useState(initialDataset?.id)
  const [versionId, setVersionId] = useState(initialVersion?.id)
  const [versions, setVersions] = useState<DatasetVersion[]>(initialVersion ? [initialVersion] : [])
  const [seed, setSeed] = useState<ContractSeed | null>(null)
  const [manual, setManual] = useState(false)
  const [registry, setRegistry] = useState<boolean>()
  const [refresh, setRefresh] = useState(0)
  const [datasetsError, setDatasetsError] = useState('')
  const [versionsError, setVersionsError] = useState('')
  const [loadingVersions, setLoadingVersions] = useState(Boolean(initialDataset))
  const dataset = datasets.find((item) => item.id === datasetId)
  const published = versions.filter((item) => item.dataset_id === datasetId && item.status === 'PUBLISHED')
    .sort((left, right) => (right.published_at ?? '').localeCompare(left.published_at ?? '') || right.id.localeCompare(left.id))
  const version = dataset?.enabled === false ? undefined : versionId
    ? published.find((item) => item.id === versionId)
    : published[0]

  useEffect(() => {
    let active = true
    listDatasets().then(
      (items) => { if (active) setDatasets(items) },
      (error: Error) => { if (active) setDatasetsError(error.message) },
    )
    fetchMetrics().then(
      () => { if (active) setRegistry(true) },
      () => { if (active) setRegistry(false) },
    )
    return () => { active = false }
  }, [refresh])

  useEffect(() => {
    if (!datasetId) return
    let active = true
    listDatasetVersions(datasetId).then(
      (items) => { if (active) setVersions(items) },
      (error: Error) => { if (active) setVersionsError(error.message) },
    ).finally(() => { if (active) setLoadingVersions(false) })
    return () => { active = false }
  }, [datasetId, refresh])

  return <div className="qa-evaluation">
    <header className="qa-evaluation-heading">
      <div className="qa-evaluation-title">
        <h1>评测工作台</h1>
        <p>选择数据集版本，配置质量契约，并查看评测任务和结果。</p>
      </div>
      <Tag color={registry ? 'green' : registry === false ? 'warning' : 'default'}>
        {registry ? 'Registry 已连接' : registry === false ? 'Registry 未连接' : 'Registry 连接中'}
      </Tag>
    </header>
    <Tabs className="qa-evaluation-tabs" activeKey={tab} onChange={setTab} items={[
      { key: 'dataset', label: <><BarChartOutlined /> 数据集评测</> },
      { key: 'metrics', label: <><ControlOutlined /> 指标管理</> },
      { key: 'tasks', label: <><ProfileOutlined /> 评测任务</> },
      { key: 'results', label: <><AuditOutlined /> 评测结果</> },
    ]} />
    {tab === 'dataset' && <Space orientation="vertical" size="middle" className="qa-full">
      <Card title="数据集上下文" extra={<Space wrap>
        <Button disabled={!dataset} onClick={() => onOpenDatasets(dataset)}>管理评测版本</Button>
        <Button icon={<ReloadOutlined />} onClick={() => {
          setDatasetsError('')
          setVersionsError('')
          setLoadingVersions(Boolean(datasetId))
          setRefresh((value) => value + 1)
        }}>刷新</Button>
        <Button onClick={() => onOpenDatasets()}>前往数据管理</Button>
      </Space>}>
        {(datasetsError || versionsError) && <Alert type="error" showIcon message={datasetsError || versionsError} />}
        <div className="qa-dataset-fields">
          <label>评测数据集 <Select showSearch optionFilterProp="label" allowClear placeholder="选择数据集" value={datasetId}
            options={datasets.map(({ id, name }) => ({ value: id, label: name }))}
            onChange={(id) => { setDatasetId(id); setVersionId(undefined); setVersions([]); setVersionsError(''); setLoadingVersions(Boolean(id)); setSeed(null) }} /></label>
          <label>评测版本 <Select allowClear loading={loadingVersions} placeholder="选择已发布版本" value={versionId ?? (version ? 'latest' : undefined)}
            disabled={!dataset || dataset.enabled === false} options={[...(published.length ? [{ value: 'latest', label: `最新已发布版本（${published[0].version}）` }] : []), ...published.map((item) => ({ value: item.id, label: item.version }))]}
            onChange={(id) => { setVersionId(id === 'latest' ? undefined : id); setSeed(null) }} /></label>
        </div>
        {!version && <Empty className="qa-context-empty" description={dataset ? (dataset.enabled === false ? '数据集已停用' : '请选择已发布的版本，或前往数据管理发布版本') : '请选择要评测的数据集'} />}
      </Card>
      {version && dataset && <AgentEvaluationWorkspace key={version.id} dataset={dataset} version={version}
        onBack={initialVersion?.id === version.id ? onBackToVersion : undefined} />}
      <div className={version ? '' : 'qa-evaluation-panels'}>
        {!version && <Card title="Agent 协作"><Alert showIcon type="info" message="请选择已发布的版本，随后可以创建 Agent 会话。" /></Card>}
        <Card title="人工评测配置" extra={<Button type="primary" onClick={() => setManual(true)}>从指标库选择</Button>}>
          {seed ? <QualityContractBuilderPage key={version?.id ?? 'manual'} seed={seed} initialVersionId={version?.id} onBack={() => setSeed(null)} />
            : manual ? <MetricRegistryPage onBuildContract={(value) => { setSeed(value); setManual(false) }} />
              : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从指标库选择指标和算子，配置质量契约" />}
        </Card>
      </div>
    </Space>}
    {tab === 'metrics' && <Card><MetricRegistryPage onBuildContract={(value) => { setSeed(value); setTab('dataset') }} /></Card>}
    {tab === 'tasks' && <Card><EvaluationResultsPage key="tasks" mode="tasks" /></Card>}
    {tab === 'results' && <Card><EvaluationResultsPage key="results" mode="results" /></Card>}
  </div>
}
