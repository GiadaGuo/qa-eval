import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Input, Modal, Popconfirm, Select, Space, Table, Tag, Upload, message } from 'antd'

import { listDatasetVersions, type Dataset, type DatasetVersion } from '../api/datasetVersions'
import { listDocumentReportTasks, type DocumentReportTask } from '../api/documentReportTasks'
import { DOCUMENT_MAX_SIZE_BYTES, deleteDocumentVersion, downloadDocumentVersion, listDocumentVersions, uploadDocumentVersion, type DocumentVersionInfo } from '../api/documentVersions'
import { createEvaluationRun, getEvaluationRun, listEvaluationRuns, type EvaluationRun } from '../api/evaluationRuns'
import { fetchCalculators } from '../api/registry'
import { combineFormalReports, finalizeDocument, getStages, listFormalReports, publishDocumentContract, type FormalReport } from '../api/formalDocumentReports'

import { getPublishedQualityContract } from '../api/qualityContracts'
import { DocumentMetricDetail } from '../components/DocumentMetricDetail'

const active = (run: EvaluationRun) => ['PENDING', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
const verdicts: Record<string, string> = { PASS: '达标', FAIL: '未达标', INDETERMINATE: '待判定' }
const metricNames: Record<string, string> = { '0101': '基本信息完整性', '0102': '内容特征完整性', '0103': '建设过程完整性', '0104': '应用说明完整性' }
const metricKeys = Object.keys(metricNames)
const calculatorKeys = ['document_basic_information', 'document_content_characteristics', 'document_construction_process', 'document_application_instructions']

function points(value: string | null) {
  if (value === null) return '不可计算'
  const [numerator, denominator = '1'] = value.split('/')
  return `${(Number(numerator) / Number(denominator)).toLocaleString('zh-CN', { maximumFractionDigits: 4 })} 分`
}

export function FormalReportView({ report }: { report: FormalReport }) {
  const documentRunId = report.mode === 'DOCUMENT' ? report.run_id : report.document_snapshot?.run_id
  const [names, setNames] = useState<{ runId: string; byMetric: Record<string, Record<string, string>> }>()

  useEffect(() => {
    if (!documentRunId) return
    let current = true
    void getEvaluationRun(documentRunId)
      .then(run => getPublishedQualityContract(run.contract_version_id))
      .then(({ contract }) => {
        if (!current) return
        const byMetric = Object.fromEntries(contract.items.map(item => {
          const configuration = item.calculation?.parameters?.configuration as {
            items?: Array<{ item_code: string; item_name: string }>
          } | undefined
          return [item.key, Object.fromEntries((configuration?.items ?? []).map(check => [check.item_code, check.item_name]))]
        }))
        setNames({ runId: documentRunId, byMetric })
      }).catch(() => { /* Check codes remain available in the report snapshot. */ })
    return () => { current = false }
  }, [documentRunId])

  return <Space orientation="vertical" style={{ width: '100%' }}>
    <strong>{verdicts[report.overall_verdict]}</strong>
    {report.dimensions.map(dimension => <div key={dimension.key}>
      <h3>{dimension.key === 'DOCUMENTATION' ? '说明文档' : '基础质量'} · {points(dimension.score)} · {verdicts[dimension.verdict]}</h3>
      <p>达标线 {dimension.threshold} 分</p>
      <Collapse items={dimension.metrics.map(metric => ({
        key: metric.key,
        label: `${metric.key} ${metricNames[metric.key] ?? ''} · ${points(metric.score)} · ${metric.satisfied_aspect_count ?? '—'}/${metric.required_aspect_count ?? '—'}`,
        children: dimension.key === 'DOCUMENTATION' && metricNames[metric.key]
          ? <DocumentMetricDetail key={report.report_id} metric={metric} name={metricNames[metric.key]} score={points(metric.score)}
            itemNames={names?.runId === documentRunId ? names?.byMetric[metric.key] : undefined} />
          : <p>{metric.status}</p>,
      }))} />
    </div>)}
  </Space>
}

export default function FormalDocumentVersionsPanel({ dataset, onViewReport }: {
  dataset: Dataset
  onViewReport: (id: string) => void
}) {
  const [documents, setDocuments] = useState<DocumentVersionInfo[]>([])
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [reports, setReports] = useState<FormalReport[]>([])
  const [legacy, setLegacy] = useState<DocumentReportTask[]>([])
  const [progress, setProgress] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState<FormalReport>()
  const [history, setHistory] = useState<DocumentVersionInfo>()
  const [advanced, setAdvanced] = useState<DocumentVersionInfo>()
  const [configuration, setConfiguration] = useState('{}')
  const [combining, setCombining] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<string>()
  const [dataRun, setDataRun] = useState<string>()
  const [documentReport, setDocumentReport] = useState<string>()
  const [confirmed, setConfirmed] = useState(false)

  const refresh = async () => {
    const [docs, dataVersions, nextRuns] = await Promise.all([
      listDocumentVersions(dataset.id), listDatasetVersions(dataset.id), listEvaluationRuns(),
    ])
    const nextReports = await listFormalReports()
    setDocuments(docs)
    setVersions(dataVersions)
    setRuns(nextRuns)
    setReports(nextReports)
    setLegacy((await Promise.all(docs.map(doc => listDocumentReportTasks(doc.id).catch(() => [])))).flat())
  }

  useEffect(() => { void refresh().catch(cause => setError(String(cause))) }, [dataset.id])
  const pending = runs.filter(run => active(run) && documents.some(doc => doc.id === run.document_version_id))
  const pendingIds = runs.filter(run =>
    documents.some(doc => doc.id === run.document_version_id) &&
    (active(run) || (run.status === 'SUCCEEDED' && run.finished_at &&
      Date.now() - Date.parse(run.finished_at) < 60_000 &&
      !reports.some(report => report.mode === 'DOCUMENT' && report.run_id === run.run_id))),
  ).map(run => run.run_id).join(',')
  useEffect(() => {
    if (!pendingIds) return
    const timer = window.setInterval(() => {
      void listEvaluationRuns().then(async nextRuns => {
        const nextReports = await listFormalReports()
        setRuns(nextRuns)
        setReports(nextReports)
        const stages = await Promise.all(pendingIds.split(',').map(async id => [id, await getStages(id)] as const))
        setProgress(previous => ({
          ...previous,
          ...Object.fromEntries(stages.map(([id, rows]) => [id, `${rows.filter(row => ['SUCCEEDED', 'FAILED'].includes(row.status)).length}/4`])),
        }))
      }).catch(cause => setError(String(cause)))
    }, 2000)
    return () => window.clearInterval(timer)
  }, [pendingIds])

  const perform = async (work: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try { await work() } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }

  const start = (doc: DocumentVersionInfo, metrics = {}) => perform(async () => {
    const contract = await publishDocumentContract(doc.id, metrics)
    const run = await createEvaluationRun(contract.contract.contract_version_id)
    setRuns(previous => [run, ...previous])
    setAdvanced(undefined)
  })

  const configure = (doc: DocumentVersionInfo) => perform(async () => {
    const calculators = await fetchCalculators()
    const settings = Object.fromEntries(metricKeys.map((key, i) => {
      const calculator = calculators.find(item => item.calculator_key === calculatorKeys[i] && item.version === '1.0.0')
      return [key, { weight: 0.25, calculator_version: '1.0.0', parameters: { configuration: calculator?.parameter_schema.properties?.configuration?.default } }]
    }))
    setConfiguration(JSON.stringify(settings, null, 2))
    setAdvanced(doc)
  })

  const docLabel = (id?: string) => {
    const doc = documents.find(item => item.id === id)
    return doc ? `${doc.version} · ${doc.filename}` : id
  }
  const documentReports = reports.filter(item => item.mode === 'DOCUMENT' && documents.some(doc => doc.id === item.document_version_id))
  const completedDocumentReports = documentReports.filter(item => runs.some(run => run.run_id === item.run_id && run.status === 'SUCCEEDED'))

  return <Space orientation="vertical" style={{ width: '100%' }}>
    {error && <Alert type="error" title={error} closable onClose={() => setError('')} />}
    <Space>
      <Upload accept=".md,.pdf,.docx" showUploadList={false} beforeUpload={file => {
        if (file.size > DOCUMENT_MAX_SIZE_BYTES || !/\.(md|pdf|docx)$/i.test(file.name)) {
          message.error('请选择 2 MB 内的 MD、PDF 或 DOCX 文件')
          return false
        }
        void perform(async () => { await uploadDocumentVersion(dataset.id, file); await refresh() })
        return false
      }}>
        <Button loading={busy}>上传说明文档</Button>
      </Upload>
      <Button onClick={() => setCombining(true)}>最终报告</Button>
    </Space>
    <Table rowKey="id" dataSource={documents} pagination={false} columns={[
      { title: '版本', dataIndex: 'version' },
      { title: '文件', dataIndex: 'filename' },
      { title: '最近评测', render: (_, doc) => {
        const latest = runs.find(run => run.document_version_id === doc.id)
        const latestReport = runs
          .filter(run => run.document_version_id === doc.id)
          .map(run => documentReports.find(report => report.run_id === run.run_id))
          .find(report => report !== undefined)
        return <Space wrap>
          {latest ? <Tag>{latest.status} {progress[latest.run_id]}</Tag> : '—'}
          {latestReport && <Button type="primary" size="small" onClick={() => setShown(latestReport)}>
            {latestReport.run_id === latest?.run_id ? '查看报告' : '查看最近报告'}
          </Button>}
        </Space>
      } },
      { title: '操作', render: (_, doc) => <Space wrap>
        <Button type="link" disabled={busy || pending.some(run => run.document_version_id === doc.id)} onClick={() => void start(doc)}>正式评测</Button>
        <Button type="link" disabled={busy} onClick={() => void configure(doc)}>高级配置</Button>
        <Button type="link" onClick={() => setHistory(doc)}>评测历史</Button>
        <Button type="link" onClick={() => void perform(() => downloadDocumentVersion(doc.id, doc.filename))}>下载</Button>
        <Popconfirm title="删除此文档版本？" onConfirm={() => perform(async () => {
          await deleteDocumentVersion(doc.id)
          await refresh()
        })}><Button type="link" danger>删除</Button></Popconfirm>
      </Space> },
    ]} />
    <Modal title={advanced ? `评测配置 · ${advanced.version}` : ''} open={!!advanced} onCancel={() => setAdvanced(undefined)}
      confirmLoading={busy} onOk={() => {
        if (!advanced) return
        try { void start(advanced, JSON.parse(configuration)) } catch { setError('检查项配置须为合法 JSON') }
      }}>
      <p>修改默认检查项时，请更新 configuration.version。权重合计须为 1。</p>
      <Input.TextArea aria-label="检查项与权重" rows={16} value={configuration} onChange={event => setConfiguration(event.target.value)} />
    </Modal>
    <Modal title={history ? `评测历史 · ${history.version}` : ''} open={!!history} onCancel={() => setHistory(undefined)} footer={null}>
      {runs.filter(run => run.document_version_id === history?.id).map(run => {
        const report = documentReports.find(item => item.run_id === run.run_id)
        return <div key={run.run_id}>
          {new Date(run.created_at).toLocaleString()} <Tag>{run.status}</Tag>
          {report ? <Button type="link" onClick={() => setShown(report)}>查看报告</Button>
            : !active(run) && <Button type="link" onClick={() => void perform(async () => {
              const restored = await finalizeDocument(run.run_id)
              setShown(restored)
              await refresh()
            })}>继续定稿</Button>}
        </div>
      })}
      {legacy.filter(task => task.document_version_id === history?.id).map(task => <div key={task.task_id}>
        历史 MVP · {task.status}
        {task.report_id && <Button type="link" onClick={() => onViewReport(task.report_id!)}>查看旧报告</Button>}
      </div>)}
    </Modal>
    <Modal title="最终报告" open={combining} onCancel={() => setCombining(false)} footer={null}>
      <Space orientation="vertical" style={{ width: '100%' }}>
        <Select aria-label="数据集版本" placeholder="数据集版本" style={{ width: '100%' }} value={selectedVersion}
          options={versions.map(version => ({ value: version.id, label: version.version }))}
          onChange={value => { setSelectedVersion(value); setDataRun(undefined); setConfirmed(false) }} />
        <Select aria-label="数据质量评测" placeholder="数据质量评测" style={{ width: '100%' }} value={dataRun}
          options={runs.filter(run => run.dataset_version_id === selectedVersion && !active(run)).map(run => ({ value: run.run_id, label: `${new Date(run.created_at).toLocaleString()} · ${run.status} · ${run.run_id.slice(-8)}` }))}
          onChange={value => { setDataRun(value); setConfirmed(false) }} />
        <Select aria-label="文档评测报告" placeholder="文档评测报告" style={{ width: '100%' }} value={documentReport}
          options={completedDocumentReports.map(report => ({ value: report.report_id, label: `${docLabel(report.document_version_id)} · ${new Date(report.finalized_at).toLocaleString()}` }))}
          onChange={value => { setDocumentReport(value); setConfirmed(false) }} />
        <Checkbox checked={confirmed} onChange={event => setConfirmed(event.target.checked)}>确认所选文档版本适用于此数据集版本</Checkbox>
        <Button type="primary" loading={busy} disabled={!confirmed || !selectedVersion || !dataRun || !documentReport}
          onClick={() => void perform(async () => {
            const result = await combineFormalReports(dataRun!, selectedVersion!, documentReport!)
            setShown(result)
            await refresh()
          })}>汇聚评测结果</Button>
        {reports.filter(report => report.dataset_snapshot && versions.some(version => version.id === report.dataset_version_id)).map(report =>
          <Button key={report.report_id} type="link" onClick={() => setShown(report)}>
            查看历史汇聚 · {new Date(report.finalized_at).toLocaleString()}
          </Button>)}
      </Space>
    </Modal>
    <Modal title={shown?.mode === 'DOCUMENT' ? `说明文档报告 · ${docLabel(shown.document_version_id)}` : `最终报告 · ${docLabel(shown?.document_snapshot?.version_id) ?? ''}`}
      open={!!shown} onCancel={() => setShown(undefined)} footer={null} width={850}>
      {shown && <FormalReportView report={shown} />}
    </Modal>
  </Space>
}
