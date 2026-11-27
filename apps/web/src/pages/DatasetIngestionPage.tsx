import { useSearchParams } from 'react-router-dom'
import { useWorkCheckpoint } from '../api/workspace'
import { useEffect, useState } from 'react'
import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Steps, Table, Typography } from 'antd'
import { createDataset, createDatasetVersion, getDataset, getDatasetVersion, getFileSourceConfig, discoverDatabaseSource, startDatabaseSnapshot, getFilePublication, startFilePublication, uploadRawFile, type DatabaseConnection, type Dataset, type DatasetCreate, type FilePublication } from '../api/datasetVersions'
import MysqlDatasourcePage from './MysqlDatasourcePage'

type IngestionValues = DatasetCreate & { connection?: DatabaseConnection }

type Props = { onBack: () => void; onComplete: (dataset: Dataset) => void }

export default function DatasetIngestionPage({ onBack, onComplete }: Props) {
  const [query] = useSearchParams()
  const resumeVersion = query.get('version')
  const [form] = Form.useForm<IngestionValues>()
  const engine = Form.useWatch(['connection', 'engine'], form)
  const host = Form.useWatch(['connection', 'host'], form)
  const port = Form.useWatch(['connection', 'port'], form)
  const database = Form.useWatch(['connection', 'database'], form)
  const displayHost = host?.includes(':') ? `[${host}]` : host
  const [step, setStep] = useState(0)
  const [dataset, setDataset] = useState<Dataset>()
  const [versionId, setVersionId] = useState<string>()
  const [source, setSource] = useState<'file' | 'database'>('file')
  const [discovery, setDiscovery] = useState<Awaited<ReturnType<typeof discoverDatabaseSource>>>()
  const [connectionTest, setConnectionTest] = useState<'testing' | 'passed' | 'failed'>()
  const [table, setTable] = useState<string>()
  const [storage, setStorage] = useState<'file' | 'sql'>('file')
  const [resumedRawFormat, setResumedRawFormat] = useState<string | null>()
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [publication, setPublication] = useState<FilePublication>()

  useWorkCheckpoint('ingestion', versionId)
  useEffect(() => {
    if (!resumeVersion) return
    let active = true
    void (async () => {
      const version = await getDatasetVersion(resumeVersion)
      const [target, current, config] = await Promise.all([
        getDataset(version.dataset_id), getFilePublication(resumeVersion), getFileSourceConfig(version.dataset_id),
      ])
      if (!active) return
      setDataset(target)
      setVersionId(version.id)
      if (target.source_kind === 'database') setSource('database')
      setTable(config.table ?? undefined)
      setStorage(config.storage ?? 'file')
      if (version.has_raw_file) setResumedRawFormat(version.raw_format)

      setPublication(current)
      if (version.status === 'PUBLISHED') setStep(2)
      else if (version.has_field_mapping || current.status === 'RUNNING') setStep(2)
      else if (version.has_raw_file || current.status === 'NEEDS_MAPPING') setStep(1)
    })().catch((failure: Error) => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [resumeVersion])

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
    if (publication?.status === 'PUBLISHED') setStep(2)
    else if (step === 0 && publication?.status === 'NEEDS_MAPPING') setStep(1)
  }, [step, publication?.status])

  async function probe(table?: string) {
    setBusy(true)
    setError('')
    const testingConnection = table === undefined
    if (testingConnection) {
      setConnectionTest('testing')
      setDiscovery(undefined)
      setTable(undefined)
    }
    try {
      const values = await form.validateFields(
        ['engine', 'host', 'port', 'database', 'user', 'password'].map((name) => ['connection', name]),
      )
      if (!values.connection) throw new Error('请填写数据库连接信息')
      const result = await discoverDatabaseSource(values.connection, table)
      setDiscovery(result)
      setTable(result.table ?? (result.tables.length === 1 ? result.tables[0] : undefined))
      if (testingConnection) setConnectionTest('passed')
    } catch (err) {
      if (testingConnection) setConnectionTest('failed')
      setError(err instanceof Error ? err.message : '请检查数据库连接信息')
    }
    finally { setBusy(false) }
  }

  async function inspect({ connection, ...values }: IngestionValues) {
    if (source === 'file' && !file) { setError('请选择文件'); return }
    if (source === 'database' && (!table || (!versionId && (!discovery || !connection)))) { setError('请先测试连接并选择数据表'); return }
    setBusy(true)
    setError('')
    try {
      const currentDataset = dataset ?? await createDataset(values)
      setDataset(currentDataset)
      if (source === 'database') {
        const result = await startDatabaseSnapshot(currentDataset.id, table, versionId, versionId ? undefined : connection)
        form.setFieldValue(['connection', 'password'], undefined)
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

  async function publishRaw() {
    if (!versionId) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`/api/v1/dataset-versions/${versionId}/publish?record_schema=3.0`, { method: 'POST' })
      if (!response.ok) {
        const body = await response.json() as { detail?: string }
        throw new Error(body.detail ?? '原始数据发布失败')
      }
      setPublication({ status: 'PUBLISHED' })
      setStep(2)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '原始数据发布失败')
    } finally { setBusy(false) }
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
      {step === 0 && <Form<IngestionValues> form={form} layout="vertical" onFinish={inspect}
        initialValues={{ connection: { engine: 'mysql', port: 3306 } }}
        onValuesChange={(changed) => {
          if (changed.connection) {
            setDiscovery(undefined)
            setTable(undefined)
            setConnectionTest(undefined)
            setError('')
          }
        }}>
        <Form.Item label="数据来源"><Select value={source} disabled={Boolean(dataset) || busy} onChange={setSource}
          options={[{ value: 'file', label: '文件上传' }, { value: 'database', label: '数据库直连' }]} /></Form.Item>
        {source === 'database' ? <>
          {!versionId && <Card size="small" title="数据库连接信息">
            <Row gutter={[12, 0]} align="bottom">
              <Col xs={24} sm={12} xl={4}>
                <Form.Item name={['connection', 'engine']} label="数据库类型" rules={[{ required: true }]}>
                  <Select disabled={busy} onChange={(engine) => form.setFieldValue(['connection', 'port'], engine === 'mysql' ? 3306 : 5432)}
                    options={[{ value: 'mysql', label: 'MySQL' }, { value: 'postgresql', label: 'PostgreSQL' }]} />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12} xl={5}><Form.Item name={['connection', 'host']} label="主机 / IP" rules={[{ required: true, whitespace: true }]}><Input disabled={busy} maxLength={255} /></Form.Item></Col>
              <Col xs={12} sm={6} xl={3}><Form.Item name={['connection', 'port']} label="端口" rules={[{ required: true, type: 'integer', min: 1, max: 65535 }]}><InputNumber disabled={busy} min={1} max={65535} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6} xl={4}><Form.Item name={['connection', 'database']} label="数据库名" rules={[{ required: true }]}><Input disabled={busy} maxLength={128} /></Form.Item></Col>
              <Col xs={12} sm={6} xl={4}><Form.Item name={['connection', 'user']} label="账号" rules={[{ required: true }]}><Input disabled={busy} maxLength={128} autoComplete="off" /></Form.Item></Col>
              <Col xs={12} sm={6} xl={4}><Form.Item name={['connection', 'password']} label="密码" rules={[{ required: true }]}><Input.Password disabled={busy} maxLength={1024} autoComplete="new-password" /></Form.Item></Col>
            </Row>
            {engine && host && port && database && <Form.Item label="数据链接">
              <Input readOnly value={`${engine}://${displayHost}:${port}/${encodeURIComponent(database)}`} />
            </Form.Item>}
            <Space wrap>
              <Button disabled={busy} onClick={() => void probe()}>测试连接</Button>
              <span role="status" aria-live="polite">
                {connectionTest === 'testing' && <Typography.Text type="secondary">
                  <LoadingOutlined aria-hidden /> 正在测试连接
                </Typography.Text>}
                {connectionTest === 'passed' && <Typography.Text type="success">
                  <CheckCircleOutlined aria-hidden /> 连接成功
                </Typography.Text>}
                {connectionTest === 'failed' && <Typography.Text type="danger">
                  <CloseCircleOutlined aria-hidden /> 连接失败，请重试
                </Typography.Text>}
              </span>
            </Space>
          </Card>}
          {discovery && <Form.Item label="目标表" required><Select value={table} disabled={Boolean(versionId)}
            options={discovery.tables.map((name) => ({ value: name, label: name }))}
            onChange={(value) => { setTable(value); setDiscovery({ ...discovery, fields: undefined, preview: undefined }) }} /></Form.Item>}
          {table && !versionId && <Button loading={busy} onClick={() => void probe(table)}>探测字段与样本</Button>}
          {discovery?.fields && <Table size="small" pagination={false} rowKey="name" dataSource={discovery.fields}
            columns={[{ title: '字段', dataIndex: 'name' }, { title: '类型', dataIndex: 'data_type' },
              { title: '样例', render: (_, field) => JSON.stringify(discovery.preview?.[0]?.[field.name]) }]} />}
          {publication?.status === 'RUNNING' && <Alert type="info" title="正在读取只读一致性快照"
            description={
              <div>
                <progress aria-label="快照读取进度" style={{ width: '100%', accentColor: 'currentColor' }} />
                {publication.record_count != null && <div>已读取 {publication.record_count.toLocaleString('zh-CN')} 条记录</div>}
              </div>
            } />}
          {publication?.status === 'FAILED' && <Alert type="error" title={publication.reason} />}
        </> : <>
        <Form.Item label="存储方式">
          <Select value={storage} onChange={setStorage} options={[
            { value: 'file', label: '文件存储' }, { value: 'sql', label: 'SQL 存储' },
          ]} />
        </Form.Item>
        <Form.Item label="源文件" required><input type="file" accept=".jsonl,.json,.parquet,.csv" onChange={(event) => setFile(event.target.files?.[0])} /></Form.Item>
        </>}
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
        <Button type="primary" htmlType="submit" loading={busy || publication?.status === 'RUNNING'}>
          {source === 'file' ? '上传并探测' : publication?.status === 'FAILED' ? '重试生成快照' : '读取快照并映射'}
        </Button>
      </Form>}
      {step === 1 && source === 'file' && storage === 'file' && (file?.name.toLowerCase().endsWith('.jsonl') || resumedRawFormat === 'jsonl')
        && <Button loading={busy} onClick={() => void publishRaw()}>发布原始数据（v3）</Button>}
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
