/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：数据集版本列表、上传原始 JSONL、发布版本。
 */

import { useEffect, useRef, useState } from 'react'
import type { ComponentType, HTMLAttributes } from 'react'
import {
  ArrowLeftOutlined,
  CloudUploadOutlined,
  EyeOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Modal,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'

import {
  createDatasetVersion,
  deleteDatasetVersion,
  previewVersionDeletion,
  type VersionDeletionPreview,
  listDatasetVersions,
  startFilePublication,
  applyDefaultFileMapping,
  startDatabaseSnapshot,
  probeSavedDatabase,
  getFileSourceConfig,
  updateFileSourceConfig,
  getFieldMapping,
  type FileSourceConfig,
  type FieldMappingSpec,
  uploadRawFile,
  type Dataset,
  type DatasetVersion,
} from '../api/datasetVersions'
import FormalDocumentVersionsPanel from './FormalDocumentVersionsPanel'
import ResizableHeaderCell, { type ResizableHeaderCellProps } from '../components/ResizableHeaderCell'
import { datasetVersionStatusLabel, formatModalityList } from '../utils/displayLabels'

const { Title } = Typography

interface DatasetDetailPageProps {
  dataset: Dataset
  onBack: () => void
  onEvaluate?: () => void
  onSelectVersion: (version: DatasetVersion) => void
  onConfigureMapping: (version: DatasetVersion) => void
  onViewReport: (reportId: string) => void
}

const statusColor: Record<string, string> = {
  DRAFT: 'default',
  PUBLISHED: 'success',
}

const COLUMN_WIDTHS_STORAGE_KEY = 'qa-eval.dataset-versions.column-widths.v1'
const defaultColumnWidths: Record<string, number> = {
  version: 140,
  status: 120,
  setup: 260,
  created_at: 190,
  published_at: 190,
  action: 560,
}
const minimumColumnWidths: Record<string, number> = {
  version: 100,
  status: 90,
  setup: 210,
  created_at: 160,
  published_at: 160,
  action: 320,
}

const loadColumnWidths = () => {
  try {
    const stored = JSON.parse(localStorage.getItem(COLUMN_WIDTHS_STORAGE_KEY) ?? '{}') as Record<string, unknown>
    return Object.fromEntries(
      Object.entries(defaultColumnWidths).map(([key, fallback]) => [
        key,
        typeof stored[key] === 'number' && Number.isFinite(stored[key])
          ? Math.max(minimumColumnWidths[key], stored[key])
          : fallback,
      ]),
    )
  } catch {
    return { ...defaultColumnWidths }
  }
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

function DataSourcePanel({ dataset, versions }: { dataset: Dataset; versions: DatasetVersion[] }) {
  const [config, setConfig] = useState<FileSourceConfig>()
  const [inspection, setInspection] = useState<Awaited<ReturnType<typeof probeSavedDatabase>>>()
  const [reference, setReference] = useState<string>()
  const [mapping, setMapping] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    getFileSourceConfig(dataset.id, controller.signal).then((value) => {
      if (controller.signal.aborted) return
      setConfig(value)
      setReference(value.reference_version_id ?? undefined)
      setMapping(value.mapping ? JSON.stringify(value.mapping, null, 2) : '')
    }).catch((err: Error) => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [dataset.id])

  async function selectReference(id: string) {
    setError('')
    setBusy(true)
    try {
      const value = await getFieldMapping(id)
      setReference(id)
      setMapping(value ? JSON.stringify(value, null, 2) : '')
    } catch (err) { setError(err instanceof Error ? err.message : '加载映射失败') }
    finally { setBusy(false) }
  }

  async function probe() {
    if (!config?.table) return
    setBusy(true)
    setError('')
    try {
      const result = await probeSavedDatabase(dataset.id)
      setInspection(result)
      message.success(`探测到 ${result.fields?.length ?? 0} 个字段、${result.preview?.length ?? 0} 条样例`)
    } catch (err) { setError(err instanceof Error ? err.message : '探测失败') }
    finally { setBusy(false) }
  }

  async function save() {
    if (!reference) return
    setError('')
    setBusy(true)
    try {
      setConfig(await updateFileSourceConfig(dataset.id, reference, JSON.parse(mapping) as FieldMappingSpec))
      message.success('默认映射已更新，将用于后续版本')
    } catch (err) { setError(err instanceof Error ? err.message : '保存失败') }
    finally { setBusy(false) }
  }

  if (error && !config) return <Alert type="error" showIcon title={error} />
  if (!config) return <Spin />
  if (!config.storage) return <Alert type="info" showIcon title="数据源待配置" />
  const fields = inspection?.fields ?? config.fields ?? []
  return <Space orientation="vertical" style={{ width: '100%' }}>
    {error && <Alert type="error" showIcon title={error} />}
    <Descriptions size="small" bordered items={[
      { key: 'kind', label: '来源', children: config.table ? (config.engine === 'postgresql' ? 'PostgreSQL 数据库直连' : 'MySQL 数据库直连') : '文件上传' },
      { key: 'storage', label: '存储方式', children: config.storage === 'sql' ? 'SQL 存储' : '文件存储' },
      { key: 'path', label: config.table ? '目标表' : '存储路径', children: config.table ?? dataset.storage_path ?? '—' },
    ]} />
    {config.table && <Button loading={busy} onClick={() => void probe()}>重新探测数据库</Button>}
    {inspection && <Table
      title={() => <Typography.Text strong>数据样例（{inspection.preview.length} 条）</Typography.Text>}
      size="small" pagination={false} rowKey="index"
      dataSource={inspection.preview.map((row, index) => ({ row, index }))}
      locale={{ emptyText: '目标表未返回样例' }}
      tableLayout="fixed" scroll={{ x: Math.max(fields.length * 200, 640) }}
      columns={fields.map(({ name }) => ({
        title: name, key: name, width: 200,
        render: (_, { row }) => {
          const value = row[name]
          return <div style={{ maxHeight: 80, overflow: 'auto', overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
            {value === undefined ? '—' : typeof value === 'string' ? value : JSON.stringify(value)}
          </div>
        },
      }))}
    />}
    <Table title={() => <Typography.Text strong>字段结构（{fields.length} 个）</Typography.Text>}
      size="small" pagination={fields.length > 10 ? { pageSize: 10 } : false} rowKey="name" dataSource={fields} columns={[
      { title: '字段', dataIndex: 'name' },
      { title: '类型', dataIndex: 'data_type' },
      { title: '可空', dataIndex: 'nullable', render: (value: boolean) => value ? '是' : '否' },
    ]} />
    <Select aria-label="Schema 参考版本" placeholder="选择 Schema 参考版本" value={reference} disabled={busy}
      options={versions.filter((version) => version.status === 'PUBLISHED').map((version) => ({ value: version.id, label: version.version }))}
      onChange={(id) => void selectReference(id)} style={{ width: 220 }} />
    <Input.TextArea aria-label="默认字段映射" rows={10} value={mapping} disabled={busy} onChange={(event) => setMapping(event.target.value)} />
    <Button type="primary" disabled={!reference} loading={busy} onClick={() => void save()}>保存默认映射</Button>
  </Space>
}

export default function DatasetDetailPage({
  dataset,
  onBack,
  onEvaluate,
  onSelectVersion,
  onConfigureMapping,
  onViewReport,
}: DatasetDetailPageProps) {
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [uploadingMap, setUploadingMap] = useState<Record<string, boolean>>({})
  const [uploadProgressMap, setUploadProgressMap] = useState<Record<string, {
    percent: number
    status: 'active' | 'success' | 'exception'
  }>>({})
  const [publishingMap, setPublishingMap] = useState<Record<string, boolean>>({})
  const [deletingMap, setDeletingMap] = useState<Record<string, boolean>>({})
  const fileMapRef = useRef<Record<string, File>>({})
  const [selectedFileMap, setSelectedFileMap] = useState<Record<string, string>>({})
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(loadColumnWidths)

  useEffect(() => {
    localStorage.setItem(COLUMN_WIDTHS_STORAGE_KEY, JSON.stringify(columnWidths))
  }, [columnWidths])

  useEffect(() => {
    let cancelled = false
    listDatasetVersions(dataset.id)
      .then((data) => {
        if (!cancelled) setVersions(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dataset.id])

  const handleCreateVersion = async (file: File) => {
    try {
      setLoading(true)
      const version = await createDatasetVersion(dataset.id)
      await uploadRawFile(version.id, file)
      const mapping = await applyDefaultFileMapping(version.id)
      if (mapping.applied) await startFilePublication(version.id)
      else {
        message.info(mapping.reason)
        onConfigureMapping(version)
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    } finally {
      try { setVersions(await listDatasetVersions(dataset.id)) }
      catch (err) { message.error(err instanceof Error ? err.message : '加载版本失败') }
      finally { setLoading(false) }
    }
  }

  const handleUpload = async (versionId: string) => {
    const file = fileMapRef.current[versionId]
    if (!file) {
      message.warning('请选择 JSONL、JSON、Parquet 或 CSV 文件')
      return
    }
    setUploadingMap((prev) => ({ ...prev, [versionId]: true }))
    setUploadProgressMap((prev) => ({
      ...prev,
      [versionId]: { percent: 0, status: 'active' },
    }))
    try {
      const result = await uploadRawFile(versionId, file, (percent) => {
        setUploadProgressMap((prev) => ({
          ...prev,
          [versionId]: { percent, status: percent === 100 ? 'success' : 'active' },
        }))
      })
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success(`上传成功：${result.filename}（${formatBytes(result.size_bytes)}）`)
      if (dataset.source_kind === 'file') {
        const mapping = await applyDefaultFileMapping(versionId)
        if (mapping.applied) {
          await startFilePublication(versionId)
          setVersions(await listDatasetVersions(dataset.id))
        } else {
          message.info(mapping.reason)
        }
      }
    } catch (err) {
      setUploadProgressMap((prev) => ({
        ...prev,
        [versionId]: {
          percent: prev[versionId]?.percent ?? 0,
          status: 'exception',
        },
      }))
      message.error(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploadingMap((prev) => ({ ...prev, [versionId]: false }))
    }
  }

  const handleDatabaseSnapshot = async (retryVersionId?: string) => {
    setLoading(true)
    try {
      await startDatabaseSnapshot(dataset.id, undefined, retryVersionId)
      setVersions(await listDatasetVersions(dataset.id))
    } catch (err) { message.error(err instanceof Error ? err.message : '生成快照失败') }
    finally { setLoading(false) }
  }

  const handlePublish = async (versionId: string) => {
    setPublishingMap((prev) => ({ ...prev, [versionId]: true }))
    try {
      const publication = await startFilePublication(versionId)
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success(publication.status === 'PUBLISHED' ? '版本已发布' : '发布任务已提交')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '发布失败')
    } finally {
      setPublishingMap((prev) => ({ ...prev, [versionId]: false }))
    }
  }

  const [deleteTarget, setDeleteTarget] = useState<DatasetVersion>()
  const [deletionPreview, setDeletionPreview] = useState<VersionDeletionPreview>()
  const deletionRequest = useRef(0)
  const [deletionError, setDeletionError] = useState('')

  const openVersionDeletion = async (version: DatasetVersion) => {
    const request = ++deletionRequest.current
    setDeleteTarget(version)
    setDeletionPreview(undefined)
    setDeletionError('')
    try {
      const preview = await previewVersionDeletion(dataset.id, version.id)
      if (request === deletionRequest.current) setDeletionPreview(preview)
    } catch (err) {
      if (request === deletionRequest.current) setDeletionError(err instanceof Error ? err.message : '读取删除影响失败')
    }
  }

  const handleDeleteVersion = async (version: DatasetVersion) => {
    setDeletingMap((prev) => ({ ...prev, [version.id]: true }))
    try {
      if (!deletionPreview) return
      await deleteDatasetVersion(dataset.id, version.id, deletionPreview.contracts.map((contract) => contract.contract_version_id))
      setDeleteTarget(undefined)
      // 清理被删版本残留的本地状态，其他版本不受影响
      delete fileMapRef.current[version.id]
      setSelectedFileMap((prev) => {
        const next = { ...prev }
        delete next[version.id]
        return next
      })
      setUploadingMap((prev) => {
        const next = { ...prev }
        delete next[version.id]
        return next
      })
      setUploadProgressMap((prev) => {
        const next = { ...prev }
        delete next[version.id]
        return next
      })
      setPublishingMap((prev) => {
        const next = { ...prev }
        delete next[version.id]
        return next
      })
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success(`版本「${version.version}」已删除`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeletingMap((prev) => ({ ...prev, [version.id]: false }))
    }
  }

  const running = versions.some((version) => version.publication?.status === 'RUNNING')
  useEffect(() => {
    if (!running) return
    let active = true
    const timer = window.setInterval(() => {
      listDatasetVersions(dataset.id)
        .then((rows) => { if (active) setVersions(rows) })
        .catch((err: Error) => { if (active) message.error(err.message) })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [dataset.id, running])

  const baseColumns: TableColumnsType<DatasetVersion> = [
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string, row: DatasetVersion) => {
        if (row.publication?.status === 'RUNNING') return <Tag color="processing">{row.publication.stage === 'SNAPSHOT' ? '正在读取快照' : '正在标准化'}</Tag>
        if (row.publication?.status === 'NEEDS_MAPPING') return <Tag color="warning" title={row.publication.reason}>待确认映射</Tag>
        if (row.publication?.status === 'FAILED') return <Tag color="error" title={row.publication.reason}>发布失败</Tag>
        return <Tag color={statusColor[status] ?? 'default'}>{datasetVersionStatusLabel(status)}</Tag>
      },
    },
    {
      title: '记录数',
      key: 'setup',
      render: (_: unknown, row: DatasetVersion) => row.record_count ?? row.publication?.record_count ?? '—',
    },
    {
      title: '快照时间',
      dataIndex: 'snapshot_at',
      key: 'created_at',
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: '发布时间',
      dataIndex: 'published_at',
      key: 'published_at',
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: DatasetVersion) => {
        const isDraft = record.status === 'DRAFT' && record.publication?.status !== 'RUNNING'
        const uploadProgress = uploadProgressMap[record.id]
        return (
          <div style={{ width: '100%', minWidth: 0 }}>
          <Space wrap size={[8, 8]}>
            <Button
              type="link"
              icon={<EyeOutlined />}
              onClick={() => onSelectVersion(record)}
            >
              详情
            </Button>
            <Button danger type="link" onClick={() => void openVersionDeletion(record)}>
              删除
            </Button>
            {record.record_schema === '3.0' && (
              <Button onClick={() => onConfigureMapping(record)}>确认字段映射</Button>
            )}
            {isDraft && dataset.source_kind === 'database' && (
              <Space>
                {record.publication?.status === 'FAILED' && record.publication.stage === 'SNAPSHOT' &&
                  <Button loading={loading} onClick={() => void handleDatabaseSnapshot(record.id)}>重试生成快照</Button>}
                {record.has_raw_file && (!record.has_field_mapping || record.raw_format === 'jsonl') &&
                  <Button onClick={() => onConfigureMapping(record)}>确认字段映射</Button>}
                {record.has_field_mapping && <Button loading={publishingMap[record.id]}
                  onClick={() => void handlePublish(record.id)}>重试发布</Button>}
              </Space>
            )}
            {isDraft && dataset.source_kind !== 'database' && (
              <Space>
                <Upload
                  disabled={uploadingMap[record.id]}
                  beforeUpload={(file) => {
                    fileMapRef.current[record.id] = file
                    setUploadProgressMap((previous) => {
                      const next = { ...previous }
                      delete next[record.id]
                      return next
                    })
                    setSelectedFileMap((previous) => ({
                      ...previous,
                      [record.id]: file.name,
                    }))
                    return false
                  }}
                  maxCount={1}
                  accept=".jsonl,.json,.parquet,.csv"
                  showUploadList={false}
                >
                  <Button disabled={uploadingMap[record.id]} icon={<CloudUploadOutlined />}>选择数据文件</Button>
                </Upload>
                <Typography.Text type="secondary">
                  {selectedFileMap[record.id] ?? '未选择文件'}
                </Typography.Text>
                <Button
                  loading={uploadingMap[record.id]}
                  disabled={uploadingMap[record.id]}
                  onClick={() => handleUpload(record.id)}
                >
                  上传
                </Button>
                <Button
                  disabled={!record.has_raw_file || uploadingMap[record.id]}
                  title={record.has_raw_file ? undefined : '请先上传 JSONL 或 Parquet'}
                  onClick={() => onConfigureMapping(record)}
                >
                  字段映射
                </Button>
                <Button
                  icon={<PlayCircleOutlined />}
                  disabled={!record.has_raw_file || !record.has_field_mapping || uploadingMap[record.id]}
                  title={!record.has_raw_file ? '请先上传 JSONL' : !record.has_field_mapping ? '请先保存字段映射' : undefined}
                  loading={publishingMap[record.id]}
                  onClick={() => handlePublish(record.id)}
                >
                  {record.publication?.status === 'FAILED' ? '重试发布' : '发布'}
                </Button>
              </Space>
            )}
          </Space>
          {isDraft && uploadProgress && (
            <div style={{ maxWidth: 430, marginTop: 10, padding: '8px 12px', borderRadius: 8, background: 'var(--surface-subtle, #f5f9fd)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 4 }}>
                <Typography.Text ellipsis style={{ maxWidth: 290, fontSize: 12 }} title={selectedFileMap[record.id]}>
                  {selectedFileMap[record.id]}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ flexShrink: 0, fontSize: 12 }}>
                  {uploadProgress.status === 'exception'
                    ? '上传失败'
                    : uploadProgress.percent >= 99 && uploadingMap[record.id]
                      ? '正在解析并校验'
                      : uploadProgress.percent === 100
                        ? '上传完成'
                        : '正在上传'}
                </Typography.Text>
              </div>
              <Progress
                percent={uploadProgress.percent}
                status={uploadProgress.status}
                size="small"
                strokeColor={uploadProgress.status === 'success' ? undefined : '#36749b'}
              />
            </div>
          )}
          </div>
        )
      },
    },
  ]

  const columns: TableColumnsType<DatasetVersion> = baseColumns.map((column) => {
    const key = String(column.key)
    const width = columnWidths[key]
    return {
      ...column,
      width,
      onHeaderCell: () => ({
        width,
        minWidth: minimumColumnWidths[key],
        onResize: (nextWidth: number) => {
          setColumnWidths((previous) => ({ ...previous, [key]: nextWidth }))
        },
      } as unknown as HTMLAttributes<HTMLTableCellElement>),
    }
  })

  const resetColumnWidths = () => setColumnWidths({ ...defaultColumnWidths })
  const tableWidth = Object.values(columnWidths).reduce((total, width) => total + width, 0)

  return (
    <Card
      title={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
            返回
          </Button>
          <Title level={4} style={{ margin: 0 }}>
            {dataset.name}
          </Title>
        </Space>
      }
      extra={onEvaluate && <Button type="primary" disabled={dataset.enabled === false} onClick={onEvaluate}>发起评测</Button>}
    >
      <Descriptions size="small" bordered column={2} style={{ marginBottom: 24 }}>
        <Descriptions.Item label="ID">{dataset.id}</Descriptions.Item>
        <Descriptions.Item label="模态">{formatModalityList(dataset.modality)}</Descriptions.Item>
        <Descriptions.Item label="描述">{dataset.description ?? '-'}</Descriptions.Item>
      </Descriptions>

      <Tabs
        defaultActiveKey="dataset-versions"
        items={[
          {
            key: 'dataset-versions',
            label: '数据集版本',
            children: (
              <>
                <Space wrap style={{ marginBottom: 16 }}>
                  {dataset.source_kind === 'database'
                    ? <Button type="primary" loading={loading} onClick={() => void handleDatabaseSnapshot()}>生成新版本</Button>
                    : <Upload accept=".jsonl,.json,.parquet,.csv" maxCount={1} showUploadList={false}
                    disabled={loading} beforeUpload={(file) => { void handleCreateVersion(file); return false }}>
                    <Button type="primary" icon={<CloudUploadOutlined />} loading={loading}>上传新版本</Button>
                  </Upload>}
                  <Button type="text" onClick={resetColumnWidths}>
                    重置列宽
                  </Button>
                </Space>

                {loading ? (
                  <Spin tip="加载版本中..." />
                ) : versions.length === 0 ? (
                  <Empty description="暂无版本，请创建" />
                ) : (
                  <Table
                    className="resizable-table"
                    rowKey="id"
                    dataSource={versions}
                    columns={columns}
                    components={{ header: { cell: ResizableHeaderCell as ComponentType<ResizableHeaderCellProps> } }}
                    pagination={false}
                    tableLayout="fixed"
                    scroll={{ x: tableWidth }}
                  />
                )}
              </>
            ),
          },
          {
            key: 'source-config',
            label: '数据源配置',
            children: <DataSourcePanel key={dataset.id} dataset={dataset} versions={versions} />,
          },
          {
            key: 'document-versions',
            label: '说明文档版本',
            children: <FormalDocumentVersionsPanel dataset={dataset} onViewReport={onViewReport} />,
          },
        ]}
      />
      {deleteTarget && <Modal
        open
        title={deleteTarget.publication?.status === 'RUNNING' ? `取消发布并删除「${deleteTarget.version}」？` : `删除版本「${deleteTarget.version}」？`}
        okText={deletionPreview?.contracts.length ? '删除版本及契约' : '删除版本'}
        cancelText="取消"
        confirmLoading={deletingMap[deleteTarget.id]}
        okButtonProps={{ danger: true, disabled: !deletionPreview || Object.keys(deletionPreview.blockers).length > 0 }}
        onCancel={() => {
          if (!deletingMap[deleteTarget.id]) {
            deletionRequest.current += 1
            setDeleteTarget(undefined)
          }
        }}
        onOk={() => handleDeleteVersion(deleteTarget)}
      >
        {deletionError ? <Alert type="error" title={deletionError} /> : !deletionPreview ? <Spin /> : <>
          {Object.keys(deletionPreview.blockers).length > 0
            ? <Alert type="warning" title="此版本仍被评测记录引用，或有正在执行的 Agent 调用，无法删除。" />
            : <p>{deleteTarget.publication?.status === 'RUNNING'
              ? '删除后将停止当前发布任务。'
              : '确认后将永久删除此版本。'}</p>}
          {deletionPreview.agent_sessions > 0 && !Object.keys(deletionPreview.blockers).length &&
            <p>此版本的 {deletionPreview.agent_sessions} 个 Agent 会话及会话记录将一起删除。</p>}
          {deletionPreview.contracts.length > 0 && <>
            <Typography.Text strong>绑定的质量契约（{deletionPreview.contracts.length} 个）</Typography.Text>
            <ul style={{ maxHeight: 240, overflowY: 'auto' }}>
              {deletionPreview.contracts.map((contract) => <li key={contract.contract_version_id}>
                {contract.name} · {contract.version_label}
              </li>)}
            </ul>
            {!Object.keys(deletionPreview.blockers).length && <p>确认后，上述质量契约将一起删除。</p>}
          </>}
        </>}
      </Modal>}
    </Card>
  )
}
