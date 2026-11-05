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
  DownloadOutlined,
  EyeOutlined,
  FileMarkdownOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Popconfirm,
  Progress,
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
  listDatasetVersions,
  publishVersion,
  uploadRawFile,
  type Dataset,
  type DatasetVersion,
} from '../api/datasetVersions'
import {
  createDocumentReportTask,
  getDocumentReportTask,
  isTaskFinished,
  listDocumentReportTasks,
  taskStatusText,
  type DocumentReportTask,
} from '../api/documentReportTasks'
import {
  DOCUMENT_MAX_SIZE_BYTES,
  deleteDocumentVersion,
  downloadDocumentVersion,
  listDocumentVersions,
  uploadDocumentVersion,
  type DocumentVersionInfo,
} from '../api/documentVersions'
import ResizableHeaderCell, { type ResizableHeaderCellProps } from '../components/ResizableHeaderCell'

const { Title } = Typography

// 文档评测任务轮询上限：与后端 document_task_timeout_seconds（30 分钟）对齐，
// 超过后停止轮询并提示，避免无限轮询
const DOCUMENT_POLL_TIMEOUT_MS = 30 * 60 * 1000

interface DatasetDetailPageProps {
  dataset: Dataset
  onBack: () => void
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

/** P3：说明文档版本面板（上传 + 列表 + 下载 + 删除 + 一键生成/查看报告）。 */
function DocumentVersionsPanel({
  dataset,
  onViewReport,
}: {
  dataset: Dataset
  onViewReport: (reportId: string) => void
}) {
  const [documents, setDocuments] = useState<DocumentVersionInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [deletingMap, setDeletingMap] = useState<Record<string, boolean>>({})
  const [runningMap, setRunningMap] = useState<Record<string, boolean>>({})
  const [tasksMap, setTasksMap] = useState<Record<string, DocumentReportTask>>({})
  const pollFailuresRef = useRef<Record<string, number>>({})
  const pollStartedAtRef = useRef<Record<string, number>>({})

  const refreshDocuments = async () => {
    const data = await listDocumentVersions(dataset.id)
    setDocuments(data)
    // 并行加载每个版本的最近任务，用于展示生成按钮与报告入口
    const taskLists = await Promise.all(
      data.map((doc) =>
        listDocumentReportTasks(doc.id).catch(() => [] as DocumentReportTask[]),
      ),
    )
    const nextTasks: Record<string, DocumentReportTask> = {}
    taskLists.forEach((tasks, index) => {
      if (tasks.length === 0) return
      const latest = tasks.reduce((a, b) =>
        (a.created_at ?? '') > (b.created_at ?? '') ? a : b,
      )
      nextTasks[data[index].id] = latest
    })
    setTasksMap(nextTasks)
  }

  useEffect(() => {
    let cancelled = false
    listDocumentVersions(dataset.id)
      .then((data) => {
        if (!cancelled) setDocuments(data)
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

  // 轮询未完成的任务状态；连续失败 3 次或超过整体轮询上限后本地标记为
  // FAILED，避免长期显示「评测中」/ 无限轮询
  useEffect(() => {
    const pendingTasks = Object.values(tasksMap).filter((task) => !isTaskFinished(task.status))
    if (pendingTasks.length === 0) return

    const markFailedLocally = (taskId: string, documentVersionId: string, message: string) => {
      setTasksMap((tasksPrev) => {
        const current = tasksPrev[documentVersionId]
        if (!current) return tasksPrev
        return {
          ...tasksPrev,
          [documentVersionId]: { ...current, status: 'FAILED', error: { message } },
        }
      })
      pollFailuresRef.current[taskId] = 0
    }

    const timer = setInterval(() => {
      const now = Date.now()
      void Promise.all(
        pendingTasks.map((task) => {
          // 首次进入轮询记录起点；超过上限即停止并本地置为超时失败
          const startedAt = pollStartedAtRef.current[task.task_id] ?? now
          if (pollStartedAtRef.current[task.task_id] === undefined) {
            pollStartedAtRef.current[task.task_id] = startedAt
          }
          if (now - startedAt > DOCUMENT_POLL_TIMEOUT_MS) {
            markFailedLocally(
              task.task_id,
              task.document_version_id,
              '评测超时，请刷新页面查看最终状态或重新生成',
            )
            return Promise.resolve()
          }
          return getDocumentReportTask(task.task_id)
            .then((next) => {
              setTasksMap((prev) => ({ ...prev, [next.document_version_id]: next }))
              pollFailuresRef.current[task.task_id] = 0
            })
            .catch(() => {
              const count = (pollFailuresRef.current[task.task_id] ?? 0) + 1
              if (count >= 3) {
                // 本地降级为失败，提示用户后端可能已异常
                markFailedLocally(
                  task.task_id,
                  task.document_version_id,
                  '获取任务状态失败，请刷新页面或重新生成',
                )
              } else {
                pollFailuresRef.current[task.task_id] = count
              }
            })
        }),
      )
    }, 2000)

    return () => clearInterval(timer)
  }, [tasksMap])

  const handleGenerateReport = async (record: DocumentVersionInfo) => {
    setRunningMap((prev) => ({ ...prev, [record.id]: true }))
    try {
      const task = await createDocumentReportTask(record.id)
      setTasksMap((prev) => ({ ...prev, [record.id]: task }))
      message.success('评测任务已创建，可在列表查看进度')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建评测任务失败')
    } finally {
      setRunningMap((prev) => ({ ...prev, [record.id]: false }))
    }
  }

  const startUpload = async (file: File) => {
    setUploading(true)
    try {
      const created = await uploadDocumentVersion(dataset.id, file)
      await refreshDocuments()
      message.success(`上传成功：${created.filename}（版本 ${created.version}）`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleBeforeUpload = (file: File) => {
    // 前端预校验：类型 / 大小 / 非空；服务端仍会完整校验
    if (!file.name.toLowerCase().endsWith('.md')) {
      message.warning('仅支持 Markdown（.md）文件')
      return false
    }
    if (file.size === 0) {
      message.warning('文件为空')
      return false
    }
    if (file.size > DOCUMENT_MAX_SIZE_BYTES) {
      message.warning('文件大小超过 2MB 上限')
      return false
    }
    void startUpload(file)
    return false
  }

  const handleDelete = async (record: DocumentVersionInfo) => {
    setDeletingMap((prev) => ({ ...prev, [record.id]: true }))
    try {
      await deleteDocumentVersion(record.id)
      const data = await listDocumentVersions(dataset.id)
      setDocuments(data)
      message.success(`版本「${record.version}」已删除`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeletingMap((prev) => ({ ...prev, [record.id]: false }))
    }
  }

  const columns: TableColumnsType<DocumentVersionInfo> = [
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
    },
    {
      title: '文件名',
      dataIndex: 'filename',
      key: 'filename',
      render: (value: string) => (
        <Space>
          <FileMarkdownOutlined />
          {value}
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      render: (value: number) => formatBytes(value),
    },
    {
      title: 'SHA-256',
      dataIndex: 'sha256',
      key: 'sha256',
      render: (value: string) => (
        <Typography.Text code copyable={{ text: value }} title={value}>
          {value.slice(0, 12)}…
        </Typography.Text>
      ),
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: '最近任务',
      key: 'latest_task',
      render: (_: unknown, record: DocumentVersionInfo) => {
        const task = tasksMap[record.id]
        if (!task) return '—'
        const finished = isTaskFinished(task.status)
        return (
          <Tag color={finished ? (task.status === 'FAILED' ? 'error' : 'success') : 'processing'}>
            {taskStatusText(task.status)}
          </Tag>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: DocumentVersionInfo) => {
        const task = tasksMap[record.id]
        const hasReport = task?.report_id
        return (
          <Space>
            <Button
              type="link"
              icon={<DownloadOutlined />}
              onClick={() => {
                void downloadDocumentVersion(record.id, record.filename).catch((err: unknown) =>
                  message.error(err instanceof Error ? err.message : '下载失败'),
                )
              }}
            >
              下载
            </Button>
            {hasReport ? (
              <Button
                type="link"
                onClick={() => onViewReport(task.report_id!)}
              >
                查看报告
              </Button>
            ) : (
              <Button
                type="link"
                loading={runningMap[record.id] || (task && !isTaskFinished(task.status))}
                disabled={!!task && !isTaskFinished(task.status)}
                onClick={() => handleGenerateReport(record)}
              >
                生成报告
              </Button>
            )}
            <Popconfirm
              title={`删除说明文档版本「${record.version}」？`}
              description="已被评测任务引用的版本无法删除。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleDelete(record)}
              disabled={deletingMap[record.id]}
            >
              <Button danger type="link" loading={deletingMap[record.id]}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Upload beforeUpload={handleBeforeUpload} maxCount={1} accept=".md" showUploadList={false}>
          <Button type="primary" icon={<CloudUploadOutlined />} loading={uploading}>
            上传说明文档（.md）
          </Button>
        </Upload>
        <Typography.Text type="secondary">
          仅支持 UTF-8 Markdown，≤ 2MB；每次上传生成新版本（v1、v2…）
        </Typography.Text>
      </Space>
      {loading ? (
        <Spin tip="加载说明文档中..." />
      ) : documents.length === 0 ? (
        <Empty description="暂无说明文档，请上传" />
      ) : (
        <Table rowKey="id" dataSource={documents} columns={columns} pagination={false} />
      )}
    </>
  )
}

export default function DatasetDetailPage({
  dataset,
  onBack,
  onSelectVersion,
  onConfigureMapping,
  onViewReport,
}: DatasetDetailPageProps) {
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [newVersionLabel, setNewVersionLabel] = useState('')
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

  const handleCreateVersion = async () => {
    if (!newVersionLabel.trim()) {
      message.warning('请输入版本号')
      return
    }
    try {
      await createDatasetVersion(dataset.id, newVersionLabel.trim())
      setNewVersionLabel('')
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success('版本创建成功')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    }
  }

  const handleUpload = async (versionId: string) => {
    const file = fileMapRef.current[versionId]
    if (!file) {
      message.warning('请选择 .jsonl 或 .parquet 文件')
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
      message.success(`上传成功：${result.filename}（${result.size_bytes} bytes）`)
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

  const handlePublish = async (versionId: string) => {
    setPublishingMap((prev) => ({ ...prev, [versionId]: true }))
    try {
      await publishVersion(versionId)
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success('版本发布成功')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '发布失败')
    } finally {
      setPublishingMap((prev) => ({ ...prev, [versionId]: false }))
    }
  }

  const handleDeleteVersion = async (version: DatasetVersion) => {
    setDeletingMap((prev) => ({ ...prev, [version.id]: true }))
    try {
      await deleteDatasetVersion(dataset.id, version.id)
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
      render: (status: string) => <Tag color={statusColor[status] ?? 'default'}>{status}</Tag>,
    },
    {
      title: '准备进度',
      key: 'setup',
      render: (_: unknown, record: DatasetVersion) => (
        <Space size={[4, 4]} wrap>
          <Tag color={record.has_raw_file ? 'success' : 'default'}>1 上传</Tag>
          <Tag color={record.has_field_mapping ? 'success' : 'default'}>2 字段映射</Tag>
          <Tag color={record.status === 'PUBLISHED' ? 'success' : 'default'}>3 标准化并发布</Tag>
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
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
        const isDraft = record.status === 'DRAFT'
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
            <Popconfirm
              title={`删除版本「${record.version}」？`}
              description="已被评测任务引用的版本无法删除。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleDeleteVersion(record)}
              disabled={deletingMap[record.id]}
            >
              <Button danger type="link" loading={deletingMap[record.id]}>
                删除
              </Button>
            </Popconfirm>
            {isDraft && (
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
                  accept=".jsonl,.parquet"
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
                  发布
                </Button>
              </Space>
            )}
          </Space>
          {isDraft && uploadProgress && (
            <div style={{ maxWidth: 430, marginTop: 10, padding: '8px 12px', borderRadius: 8, background: 'var(--surface-subtle, #f7f8f6)' }}>
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
                strokeColor={uploadProgress.status === 'success' ? undefined : '#547b5d'}
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
    >
      <Descriptions size="small" bordered column={2} style={{ marginBottom: 24 }}>
        <Descriptions.Item label="ID">{dataset.id}</Descriptions.Item>
        <Descriptions.Item label="模态">{dataset.modality.join(', ')}</Descriptions.Item>
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
                  <Input
                    placeholder="新版本号，例如 v1"
                    value={newVersionLabel}
                    onChange={(e) => setNewVersionLabel(e.target.value)}
                    onPressEnter={handleCreateVersion}
                  />
                  <Button type="primary" onClick={handleCreateVersion}>
                    创建版本
                  </Button>
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
            key: 'document-versions',
            label: '说明文档版本',
            children: <DocumentVersionsPanel dataset={dataset} onViewReport={onViewReport} />,
          },
        ]}
      />
    </Card>
  )
}
