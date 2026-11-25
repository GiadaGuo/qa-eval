import ManualEvaluationWorkspace from './ManualEvaluationWorkspace'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Card, Empty, Segmented, Select, Space, Tabs } from 'antd'
import { ArrowLeftOutlined, AuditOutlined, BarChartOutlined, ControlOutlined, ProfileOutlined, ReloadOutlined } from '@ant-design/icons'
import { listDatasets, listDatasetVersions, type Dataset, type DatasetVersion } from '../api/datasetVersions'
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
  const [query, setQuery] = useSearchParams()
  const requestedTab = query.get('tab')
  const tab = requestedTab === 'metrics' || requestedTab === 'tasks' || requestedTab === 'results'
    ? requestedTab : 'dataset'

  function setTab(key: string) {
    const updated = new URLSearchParams(query)
    if (key === 'dataset') updated.delete('tab')
    else updated.set('tab', key)
    setQuery(updated)
  }

  const [mode, setMode] = useState<'agent' | 'manual'>('agent')
  const [datasets, setDatasets] = useState<Dataset[]>(initialDataset ? [initialDataset] : [])
  const [datasetId, setDatasetId] = useState(initialDataset?.id)
  const [versionId, setVersionId] = useState(initialVersion?.id)
  const [versions, setVersions] = useState<DatasetVersion[]>(initialVersion ? [initialVersion] : [])
  const [seed, setSeed] = useState<ContractSeed | null>(null)
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

  const modeControl = <Segmented aria-label="评测配置模式" value={mode} onChange={setMode} options={[
    { label: 'Agent 对话', value: 'agent' },
    { label: '人工评测配置', value: 'manual' },
  ]} />

  return <div className="qa-evaluation">
    <Tabs className="qa-evaluation-tabs" activeKey={tab} onChange={setTab} items={[
      { key: 'dataset', label: <><BarChartOutlined /> 数据集评测</> },
      { key: 'metrics', label: <><ControlOutlined /> 指标管理</> },
      { key: 'tasks', label: <><ProfileOutlined /> 评测任务</> },
      { key: 'results', label: <><AuditOutlined /> 评测结果</> },
    ]} />
    {tab === 'dataset' && <Space orientation="vertical" size="middle" className="qa-full">
      <Card title="数据集上下文" extra={<Space wrap>
        {version && initialVersion?.id === version.id && <Button icon={<ArrowLeftOutlined />} onClick={onBackToVersion}>返回版本</Button>}
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
      <div hidden={mode !== 'agent'}>
        {version && dataset ? <AgentEvaluationWorkspace key={version.id} version={version} modeControl={modeControl} />
          : <Card extra={modeControl}><Alert showIcon type="info" message="请选择已发布的版本，随后可以创建 Agent 会话。" /></Card>}
      </div>
      <div hidden={mode !== 'manual'}>
        <Card title="人工评测配置" extra={modeControl}>
          {version ? <ManualEvaluationWorkspace key={version.id} version={version} />
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择已发布的数据集版本" />}
        </Card>
      </div>
    </Space>}
    {tab === 'metrics' && <Card>{seed ? <QualityContractBuilderPage seed={seed} initialVersionId={version?.id} onBack={() => setSeed(null)} /> : <MetricRegistryPage onBuildContract={setSeed} />}</Card>}
    {tab === 'tasks' && <Card><EvaluationResultsPage key="tasks" mode="tasks" /></Card>}
    {tab === 'results' && <Card><EvaluationResultsPage key="results" mode="results" /></Card>}
  </div>
}
