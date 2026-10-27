/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：数据集版本列表、上传原始 JSONL、发布版本。
 */

import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeftOutlined,
  CloudUploadOutlined,
  EyeOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'

import {
  createDatasetVersion,
  deleteDatasetVersion,
  listDatasetVersions,
  publishVersion,
  uploadRawFile,
  type Dataset,
  type DatasetVersion,
} from '../api/datasetVersions'

const { Title } = Typography

interface DatasetDetailPageProps {
  dataset: Dataset
  onBack: () => void
  onSelectVersion: (version: DatasetVersion) => void
  onConfigureMapping: (version: DatasetVersion) => void
}

const statusColor: Record<string, string> = {
  DRAFT: 'default',
  PUBLISHED: 'success',
}

export default function DatasetDetailPage({
  dataset,
  onBack,
  onSelectVersion,
  onConfigureMapping,
}: DatasetDetailPageProps) {
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [newVersionLabel, setNewVersionLabel] = useState('')
  const [uploadingMap, setUploadingMap] = useState<Record<string, boolean>>({})
  const [publishingMap, setPublishingMap] = useState<Record<string, boolean>>({})
  const [deletingMap, setDeletingMap] = useState<Record<string, boolean>>({})
  const fileMapRef = useRef<Record<string, File>>({})
  const [selectedFileMap, setSelectedFileMap] = useState<Record<string, string>>({})

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
      message.warning('请选择 .jsonl 文件')
      return
    }
    setUploadingMap((prev) => ({ ...prev, [versionId]: true }))
    try {
      const result = await uploadRawFile(versionId, file)
      const data = await listDatasetVersions(dataset.id)
      setVersions(data)
      message.success(`上传成功：${result.filename}（${result.size_bytes} bytes）`)
    } catch (err) {
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

  const columns = [
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
        return (
          <Space>
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
                  beforeUpload={(file) => {
                    fileMapRef.current[record.id] = file
                    setSelectedFileMap((previous) => ({
                      ...previous,
                      [record.id]: file.name,
                    }))
                    return false
                  }}
                  maxCount={1}
                  accept=".jsonl"
                  showUploadList={false}
                >
                  <Button icon={<CloudUploadOutlined />}>选择 JSONL</Button>
                </Upload>
                <Typography.Text type="secondary">
                  {selectedFileMap[record.id] ?? '未选择文件'}
                </Typography.Text>
                <Button
                  loading={uploadingMap[record.id]}
                  onClick={() => handleUpload(record.id)}
                >
                  上传
                </Button>
                <Button
                  disabled={!record.has_raw_file}
                  title={record.has_raw_file ? undefined : '请先上传 JSONL'}
                  onClick={() => onConfigureMapping(record)}
                >
                  字段映射
                </Button>
                <Button
                  icon={<PlayCircleOutlined />}
                  disabled={!record.has_raw_file || !record.has_field_mapping}
                  title={!record.has_raw_file ? '请先上传 JSONL' : !record.has_field_mapping ? '请先保存字段映射' : undefined}
                  loading={publishingMap[record.id]}
                  onClick={() => handlePublish(record.id)}
                >
                  发布
                </Button>
              </Space>
            )}
          </Space>
        )
      },
    },
  ]

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

      <Space style={{ marginBottom: 16 }}>
        <Input
          placeholder="新版本号，例如 v1"
          value={newVersionLabel}
          onChange={(e) => setNewVersionLabel(e.target.value)}
          onPressEnter={handleCreateVersion}
        />
        <Button type="primary" onClick={handleCreateVersion}>
          创建版本
        </Button>
      </Space>

      {loading ? (
        <Spin tip="加载版本中..." />
      ) : versions.length === 0 ? (
        <Empty description="暂无版本，请创建" />
      ) : (
        <Table rowKey="id" dataSource={versions} columns={columns} pagination={false} />
      )}
    </Card>
  )
}
