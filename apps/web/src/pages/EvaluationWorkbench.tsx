import { accountRequest } from '../api/webSession'
import { fetchBindings, fetchCalculators, fetchMetrics } from '../api/registry'
import type { OperatorDraft } from '../api/workspace'
import ManualEvaluationWorkspace from './ManualEvaluationWorkspace'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Card, Empty, Segmented, Select, Space, Tabs } from 'antd'
import { ArrowLeftOutlined, AuditOutlined, BarChartOutlined, ControlOutlined, ProfileOutlined, ReloadOutlined } from '@ant-design/icons'
import { listDatasets, listDatasetVersions, type Dataset, type DatasetVersion } from '../api/datasetVersions'
import AgentEvaluationWorkspace from './AgentEvaluationWorkspace'
import { EvaluationResultCatalogPage, EvaluationTasksPage } from './EvaluationCatalog'
import MetricRegistryPage, { type ContractSeed } from './MetricRegistryPage'
import QualityContractBuilderPage from './QualityContractBuilderPage'

async function loadOperatorDraft(id: string) {
  const [draft, metrics, calculators, bindings] = await Promise.all([
    accountRequest<OperatorDraft>('workspace/operator-drafts/' + id),
    fetchMetrics(), fetchCalculators(), fetchBindings(),
  ])
  const metric = metrics.find(item => item.metric_key === draft.metric.key && item.version === draft.metric.version)
  const resolve = (ref: OperatorDraft['calculator']) => {
    const calculator = calculators.find(item => item.calculator_key === ref.key && item.version === ref.version)
    const binding = bindings.find(item => item.metric_key === draft.metric.key && item.metric_version === draft.metric.version
      && item.calculator_key === ref.key && item.calculator_version === ref.version && item.enabled)
    return calculator && binding ? { calculator, binding } : undefined
  }
  const primary = resolve(draft.calculator)
  const operators = draft.operators.map(resolve)
  if (!metric || !primary || operators.some(operator => !operator)) throw new Error('草稿引用的指标或算子已失效')
  const seed: ContractSeed = { metric, ...primary, operators: operators.length ? operators.filter(operator => operator != null) : undefined }
  return { draft, seed }
}

type Props = {
  username: string
  initialDataset: Dataset | null
  initialVersion: DatasetVersion | null
  onOpenDatasets: (dataset?: Dataset) => void
  onBackToVersion: () => void
}

export default function EvaluationWorkbench({ username, initialDataset, initialVersion, onOpenDatasets, onBackToVersion }: Props) {
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

  const [mode, setMode] = useState<'agent' | 'manual'>(query.get('mode') === 'manual' ? 'manual' : 'agent')
  const [visitedModes, setVisitedModes] = useState<Record<string, Partial<Record<typeof mode, boolean>>>>({})
  const [datasets, setDatasets] = useState<Dataset[]>(initialDataset ? [initialDataset] : [])
  const [datasetId, setDatasetId] = useState(initialDataset?.id)
  const [versionId, setVersionId] = useState(initialVersion?.id)
  const [versions, setVersions] = useState<DatasetVersion[]>(initialVersion ? [initialVersion] : [])
  const [seed, setSeed] = useState<ContractSeed | null>(null)
  const operatorDraftId = tab === 'metrics' ? query.get('operatorDraft') : null
  const [operatorDraft, setOperatorDraft] = useState<{ id: string; value: Awaited<ReturnType<typeof loadOperatorDraft>> }>()
  const [operatorError, setOperatorError] = useState<{ id: string; message: string }>()

  useEffect(() => {
    if (!operatorDraftId) return
    let active = true
    loadOperatorDraft(operatorDraftId).then(
      value => { if (active) setOperatorDraft({ id: operatorDraftId, value }) },
      (failure: Error) => { if (active) setOperatorError({ id: operatorDraftId, message: failure.message }) },
    )
    return () => { active = false }
  }, [operatorDraftId])

  function closeOperatorDraft() {
    const updated = new URLSearchParams(query)
    updated.delete('operatorDraft')
    setQuery(updated)
  }

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

  const requestedSession = version?.id === query.get('version') ? query.get('session') : null

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

  const modeControl = <Segmented<typeof mode> aria-label="评测配置模式" value={mode} onChange={(next) => {
    if (version) setVisitedModes(current => ({ ...current, [version.id]: { ...current[version.id], [mode]: true } }))
    setMode(next)
  }} options={[
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
        {version && dataset && (mode === 'agent' || visitedModes[version.id]?.agent) ? <AgentEvaluationWorkspace active={mode === 'agent'} username={username} key={version.id + (requestedSession ?? '')} requestedSession={requestedSession} version={version} modeControl={modeControl} />
          : <Card extra={modeControl}><Alert showIcon type="info" message="请选择已发布的版本，随后可以创建 Agent 会话。" /></Card>}
      </div>
      <div hidden={mode !== 'manual'}>
        <Card title="人工评测配置" extra={modeControl}>
          {version && (mode === 'manual' || visitedModes[version.id]?.manual) ? <ManualEvaluationWorkspace active={mode === 'manual'} key={version.id + (query.get('contract') ?? '')} version={version} />
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择已发布的数据集版本" />}
        </Card>
      </div>
    </Space>}
    {tab === 'metrics' && <Card>
      {operatorDraftId
        ? operatorDraft?.id === operatorDraftId
          ? <QualityContractBuilderPage key={operatorDraftId} restoredId={operatorDraftId} initialDraft={operatorDraft.value.draft}
              initialVersionId={operatorDraft.value.draft.version_id} seed={operatorDraft.value.seed} onBack={closeOperatorDraft} />
          : operatorError?.id === operatorDraftId
            ? <Alert showIcon type="error" title={operatorError.message} action={<Button onClick={closeOperatorDraft}>返回指标管理</Button>} />
            : <Card loading />
        : seed
          ? <QualityContractBuilderPage seed={seed} initialVersionId={version?.id} onBack={() => setSeed(null)} />
          : <MetricRegistryPage onBuildContract={setSeed} />}
    </Card>}
    {tab === 'tasks' && <Card><EvaluationTasksPage key={query.get('run') ?? query.get('status') ?? ''} /></Card>}
    {tab === 'results' && <Card><EvaluationResultCatalogPage key={query.get('report') ?? ''} /></Card>}
  </div>
}
