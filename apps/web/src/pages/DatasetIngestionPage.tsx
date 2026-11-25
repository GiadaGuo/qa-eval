import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Select, Space, Steps, Table } from 'antd'
import { createDataset, createDatasetVersion, getDataset, discoverManagedMysql, startMysqlSnapshot, getFilePublication, startFilePublication, uploadRawFile, type Dataset, type DatasetCreate, type FilePublication } from '../api/datasetVersions'
import MysqlDatasourcePage from './MysqlDatasourcePage'

type Props = { onBack: () => void; onComplete: (dataset: Dataset) => void }

export default function DatasetIngestionPage({ onBack, onComplete }: Props) {
  const [step, setStep] = useState(0)
  const [dataset, setDataset] = useState<Dataset>()
  const [versionId, setVersionId] = useState<string>()
  const [source, setSource] = useState<'file' | 'database'>('file')
  const [discovery, setDiscovery] = useState<Awaited<ReturnType<typeof discoverManagedMysql>>>()
  const [table, setTable] = useState<string>()
  const [storage, setStorage] = useState<'file' | 'sql'>('file')
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [publication, setPublication] = useState<FilePublication>()

  useEffect(() => {
    if (!versionId || publication?.status !== 'RUNNING') return
    const controller = new AbortController()
    const interval = window.setInterval(() => {
      getFilePublication(versionId, controller.signal)
        .then(setPublication)
        .catch((err: Error) => { if (!controller.signal.aborted) setError(err.message) })
    }, 1500)
    return () => { controller.abort(); window.clearInterval(interval) }
  }, [versionId, publication?.status])

  useEffect(() => {
    if (step === 0 && publication?.status === 'NEEDS_MAPPING') setStep(1)
  }, [step, publication?.status])

  async function probe(table?: string) {
    setBusy(true)
    setError('')
    try {
      const result = await discoverManagedMysql(table)
      setDiscovery(result)
      setTable(result.tables.includes(result.table) ? result.table : undefined)
    } catch (err) { setError(err instanceof Error ? err.message : 'MySQL 探测失败') }
    finally { setBusy(false) }
  }

  async function inspect(values: DatasetCreate) {
    if (source === 'file' && !file) { setError('请选择文件'); return }
    if (source === 'database' && !table) { setError('请先测试连接并选择数据表'); return }
    setBusy(true)
    setError('')
    try {
      const currentDataset = dataset ?? await createDataset(values)
      setDataset(currentDataset)
      if (source === 'database') {
        const result = await startMysqlSnapshot(currentDataset.id, table, versionId)
        setVersionId(result.version_id)
        setPublication(result)
        return
      }
      if (!file) return
      const id = versionId ?? (await createDatasetVersion(currentDataset.id)).id
      setVersionId(id)
      await uploadRawFile(id, file)
      setStep(1)
    } catch (err) {
      setError(err instanceof Error ? err.message : '探测失败')
    } finally { setBusy(false) }
  }

  async function publish() {
    if (!versionId) return
    setBusy(true)
    setError('')
    try { setPublication(await startFilePublication(versionId, source === 'database' ? undefined : storage)) }
    catch (err) { setError(err instanceof Error ? err.message : '发布失败') }
    finally { setBusy(false) }
  }

  async function openPublishedDataset() {
    if (!dataset) return
    setBusy(true)
    try { onComplete(await getDataset(dataset.id)) }
    catch (err) { setError(err instanceof Error ? err.message : '加载数据集失败') }
    finally { setBusy(false) }
  }

  return <Card title="数据接入" extra={<Button onClick={onBack}>返回列表</Button>}>
    <Steps current={step} items={[
      { title: '配置数据源' }, { title: '数据探测与映射' }, { title: '确认接入' },
    ]} />
    <Space orientation="vertical" size="middle" style={{ width: '100%', marginTop: 24 }}>
      {error && <Alert type="error" showIcon title={error} />}
      {step === 0 && <Form<DatasetCreate> layout="vertical" onFinish={inspect}>
        <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} disabled={Boolean(dataset)} /></Form.Item>
        <Form.Item name="data_type" label="数据类型" rules={[{ required: true }]}><Select disabled={Boolean(dataset)} options={[
          { value: 'structured', label: '结构化' }, { value: 'semi_structured', label: '半结构化' }, { value: 'unstructured', label: '非结构化' },
        ]} /></Form.Item>
        <Form.Item name="modality" label="数据模态" rules={[{ required: true }]}><Select mode="multiple" disabled={Boolean(dataset)} options={[
          { value: 'text', label: '文本' }, { value: 'image', label: '图像' }, { value: 'audio', label: '音频' },
          { value: 'video', label: '视频' }, { value: 'structured', label: '结构化' },
        ]} /></Form.Item>
        <Form.Item name="owner" label="负责人"><Input maxLength={255} disabled={Boolean(dataset)} /></Form.Item>
        <Form.Item name="description" label="描述"><Input.TextArea disabled={Boolean(dataset)} /></Form.Item>
        <Form.Item label="数据来源"><Select value={source} disabled={Boolean(dataset)} onChange={setSource}
          options={[{ value: 'file', label: '文件上传' }, { value: 'database', label: 'MySQL 数据库直连' }]} /></Form.Item>
        {source === 'database' ? <>
          <Form.Item label="数据库连接">
            <Button loading={busy} disabled={Boolean(versionId)} onClick={() => void probe()}>测试服务端配置的 MySQL 连接</Button>
          </Form.Item>
          {discovery && <Form.Item label="目标表" required><Select value={table} disabled={Boolean(versionId)}
            options={discovery.tables.map((name) => ({ value: name, label: name }))}
            onChange={(value) => { setTable(value); setDiscovery({ ...discovery, fields: undefined, preview: undefined }) }} /></Form.Item>}
          {table && !versionId && <Button loading={busy} onClick={() => void probe(table)}>探测字段与样本</Button>}
          {discovery?.fields && <Table size="small" pagination={false} rowKey="name" dataSource={discovery.fields}
            columns={[{ title: '字段', dataIndex: 'name' }, { title: '类型', dataIndex: 'data_type' },
              { title: '样例', render: (_, field) => JSON.stringify(discovery.preview?.[0]?.[field.name]) }]} />}
          {publication?.status === 'RUNNING' && <Alert type="info" title="正在读取只读一致性快照" description={publication.record_count == null ? undefined : `已读取 ${publication.record_count} 条记录`} />}
          {publication?.status === 'FAILED' && <Alert type="error" title={publication.reason} />}
        </> : <>
        <Form.Item label="存储方式">
          <Select value={storage} onChange={setStorage} options={[
            { value: 'file', label: '文件存储' }, { value: 'sql', label: 'SQL 存储' },
          ]} />
        </Form.Item>
        <Form.Item label="源文件" required><input type="file" accept=".jsonl,.json,.parquet,.csv" onChange={(event) => setFile(event.target.files?.[0])} /></Form.Item>
        </>}
        <Button type="primary" htmlType="submit" loading={busy || publication?.status === 'RUNNING'}>
          {source === 'file' ? '上传并探测' : publication?.status === 'FAILED' ? '重试生成快照' : '读取快照并映射'}
        </Button>
      </Form>}
      {step === 1 && versionId && <MysqlDatasourcePage versionId={versionId} onMapped={() => setStep(2)} />}
      {step === 2 && <>
        {publication && <Alert showIcon
          type={publication.status === 'FAILED' ? 'error' : publication.status === 'PUBLISHED' ? 'success' : 'info'}
          title={publication.status === 'FAILED' ? publication.reason : publication.status === 'PUBLISHED' ? 'v1 已发布' : '正在标准化并生成发布产物'}
          description={publication.record_count == null ? undefined : `源文件共 ${publication.record_count} 条记录`} />}
        {publication?.status === 'PUBLISHED'
          ? <Button type="primary" loading={busy} onClick={() => void openPublishedDataset()}>查看版本</Button>
          : <Button type="primary" loading={busy || publication?.status === 'RUNNING'} onClick={() => void publish()}>
            {publication?.status === 'FAILED' ? '重试发布' : '确认接入并发布'}
          </Button>}
      </>}
    </Space>
  </Card>
}
