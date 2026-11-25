/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：数据集列表与创建页面。
 */

import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Form, Input, Modal, Select, Space, Spin, Switch, Table, Tooltip, Typography, message } from 'antd'

import { deleteDataset, getDatasetDeletionPreview, searchDatasets, updateDataset, type Dataset, type DatasetCreate, type DatasetDeletionPreview } from '../api/datasetVersions'
import { formatModalityList } from '../utils/displayLabels'

const { Title, Text } = Typography
const typeLabels: Record<string, string> = {
  structured: '结构化', semi_structured: '半结构化', unstructured: '非结构化',
}
const sourceLabels: Record<string, string> = {
  file: '文件上传', database: '数据库直连', unconfigured: '待配置',
}
const modalityLabels: Record<string, string> = {
  text: '文本', image: '图像', audio: '音频', video: '视频', structured: '结构化',
}
const options = (labels: Record<string, string>) =>
  Object.entries(labels).map(([value, label]) => ({ value, label }))

type Filters = { q?: string; data_type?: string; modality?: string; source_kind?: string; enabled?: string }
type EditValues = DatasetCreate & { enabled: boolean }

interface DatasetListPageProps {
  onIngest: () => void
  onSelectDataset: (dataset: Dataset) => void
}

export default function DatasetListPage({ onSelectDataset, onIngest }: DatasetListPageProps) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Dataset | null>(null)
  const [deletePreview, setDeletePreview] = useState<DatasetDeletionPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [form] = Form.useForm<EditValues>()
  const [filterForm] = Form.useForm<Filters>()
  const [filters, setFilters] = useState<Filters>({})
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [revision, setRevision] = useState(0)
  const [editing, setEditing] = useState<Dataset | null>(null)
  const [saving, setSaving] = useState(false)

  const openEditor = (dataset: Dataset) => {
    setEditing(dataset)
    form.resetFields()
    form.setFieldsValue({ ...dataset, enabled: dataset.enabled ?? true })
    setModalOpen(true)
  }

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ page: String(page), size: String(size) })
    Object.entries(filters).forEach(([key, value]) => {
      if (value?.trim()) params.set(key, value.trim())
    })
    setLoading(true)
    searchDatasets(params, controller.signal)
      .then(({ items, total }) => {
        if (controller.signal.aborted) return
        setDatasets(items)
        setTotal(total)
        if (!items.length && page > 1) setPage(Math.max(1, Math.ceil(total / size)))
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) message.error(err instanceof Error ? err.message : '加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [filters, page, size, revision])

  const saveDataset = async (values: EditValues) => {
    setSaving(true)
    try {
      if (editing) await updateDataset(editing.id, values)
      setModalOpen(false)
      setRevision((value) => value + 1)
      message.success('数据集已保存')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const openDeletePreview = async (dataset: Dataset) => {
    setDeleteTarget(dataset)
    setDeletePreview(null)
    setPreviewLoading(true)
    try {
      setDeletePreview(await getDatasetDeletionPreview(dataset.id))
    } catch (err) {
      setDeleteTarget(null)
      message.error(err instanceof Error ? err.message : '获取删除影响失败')
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleDelete = async (dataset: Dataset) => {
    setDeletingId(dataset.id)
    try {
      await deleteDataset(dataset.id, true)
      setRevision((value) => value + 1)
      message.success(`数据集「${dataset.name}」已删除`)
      setDeleteTarget(null)
      setDeletePreview(null)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <>
      <Card
        title={
          <Space>
            <Title level={4} style={{ margin: 0 }}>数据集管理</Title>
            <Text type="secondary">共 {total} 个</Text>
          </Space>
        }
        extra={
          <Button type="primary" onClick={onIngest}>
            数据接入
          </Button>
        }
      >
        <Form name="dataset-filters" form={filterForm} layout="inline" onFinish={(values) => { setFilters(values); setPage(1) }}>
          <Form.Item name="q"><Input allowClear placeholder="搜索名称或负责人" /></Form.Item>
          <Form.Item name="data_type"><Select allowClear placeholder="数据类型" options={options(typeLabels)} style={{ width: 125 }} /></Form.Item>
          <Form.Item name="modality"><Select allowClear placeholder="数据模态" options={options(modalityLabels)} style={{ width: 125 }} /></Form.Item>
          <Form.Item name="source_kind"><Select allowClear placeholder="数据来源" options={options(sourceLabels)} style={{ width: 125 }} /></Form.Item>
          <Form.Item name="enabled"><Select allowClear placeholder="启用状态" options={options({ true: '启用', false: '停用' })} style={{ width: 110 }} /></Form.Item>
          <Space>
            <Button htmlType="submit">搜索</Button>
            <Button onClick={() => { filterForm.resetFields(); setFilters({}); setPage(1) }}>重置</Button>
          </Space>
        </Form>
        <Table<Dataset>
          style={{ marginTop: 16 }}
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={datasets}
          scroll={{ x: 1240 }}
          pagination={{
            current: page, pageSize: size, total, showSizeChanger: true,
            onChange: (next, nextSize) => { setPage(nextSize === size ? next : 1); setSize(nextSize) },
          }}
          columns={[
            { title: '数据名称', dataIndex: 'name', ellipsis: true, width: 180 },
            { title: '数据类型', width: 110, render: (_, row) => typeLabels[row.data_type ?? ''] ?? '—' },
            { title: '数据模态', width: 120, ellipsis: true, render: (_, row) => formatModalityList(row.modality) },
            { title: '数据来源', width: 110, render: (_, row) => sourceLabels[row.source_kind ?? 'unconfigured'] },
            { title: '存储路径', width: 190, ellipsis: { showTitle: false }, render: (_, row) =>
              <Tooltip title={row.storage_path}>{row.storage_path ?? '—'}</Tooltip> },
            { title: '负责人', width: 105, dataIndex: 'owner', ellipsis: true, render: (value) => value ?? '—' },
            { title: '创建时间', width: 155, render: (_, row) => row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '—' },
            { title: '启用状态', width: 90, render: (_, row) => row.enabled === false ? '停用' : '启用' },
            { title: '操作', width: 180, fixed: 'right', render: (_, row) => (
              <Space size={0}>
                <Button type="link" size="small" onClick={() => onSelectDataset(row)}>版本管理</Button>
                <Button type="link" size="small" onClick={() => openEditor(row)}>编辑</Button>
                <Button type="link" size="small" danger disabled={previewLoading} onClick={() => void openDeletePreview(row)}>删除</Button>
              </Space>
            ) },
          ]}
        />
      </Card>

      <Modal
        title="确认删除数据集？"
        open={deleteTarget !== null}
        okText="确认删除全部"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          disabled: !deletePreview?.can_cascade_delete,
          loading: deletingId !== null,
        }}
        cancelButtonProps={{ disabled: deletingId !== null }}
        closable={deletingId === null}
        maskClosable={false}
        onCancel={() => {
          if (deletingId === null) {
            setDeleteTarget(null)
            setDeletePreview(null)
          }
        }}
        onOk={() => deleteTarget && void handleDelete(deleteTarget)}
      >
        {previewLoading || !deletePreview ? (
          <Spin tip="正在分析真实依赖关系……" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Text>数据集「{deletePreview.dataset.name}」包含以下关联资源，确认后将一并删除：</Text>
            <Descriptions column={1} size="small" bordered>
              {Object.entries(deletePreview.resources)
                .filter(([, count]) => count > 0)
                .map(([key, count]) => (
                  <Descriptions.Item key={key} label={deletionResourceLabels[key] ?? key}>
                    {count} 个
                  </Descriptions.Item>
                ))}
            </Descriptions>
            {deletePreview.blockers.length > 0 && (
              <Alert
                type="error"
                showIcon
                message="当前无法安全删除"
                description={deletePreview.blockers.map((item) => `${item.reason}：${item.count} 个`).join('；')}
              />
            )}
            <Alert type="warning" showIcon message="删除后无法恢复，请确认是否继续。" />
          </Space>
        )}
      </Modal>

      <Modal
        title="编辑数据集"
        forceRender
        confirmLoading={saving}
        open={modalOpen}
        onOk={() => form.submit()}
        onCancel={() => setModalOpen(false)}
      >
        <Form name="dataset-edit" form={form} layout="vertical" onFinish={saveDataset}>
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入数据集名称' }]}
          >
            <Input placeholder="例如 policy.jhsjk_article" />
          </Form.Item>
          <Form.Item
            label="模态"
            name="modality"
            rules={[{ required: true, message: '请选择至少一种模态' }]}
          >
            <Select mode="multiple" options={options(modalityLabels)} placeholder="请选择" />
          </Form.Item>
          <Form.Item label="数据类型" name="data_type">
            <Select allowClear options={options(typeLabels)} />
          </Form.Item>
          <Form.Item label="负责人" name="owner"><Input maxLength={255} /></Form.Item>
          {editing && <Form.Item label="启用状态" name="enabled" valuePropName="checked"><Switch /></Form.Item>}
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

const deletionResourceLabels: Record<string, string> = {
  dataset_versions: '数据集版本', dataset_manifests: '数据清单', dataset_profiles: '数据画像',
  logical_evaluation_plans: '逻辑评测计划', physical_evaluation_plans: '物理评测计划',
  evaluation_runs: '评测运行', stage_runs: '阶段运行', metric_results: '指标结果',
  rule_evaluations: '规则判定', quality_issues: '质量问题', issue_indices: '问题索引',
  quality_reports: '质量报告', scored_quality_reports: '评分报告',
  quality_contract_versions: '质量契约', agent_sessions: 'Agent 会话',
  document_versions: '说明文档版本', document_report_tasks: '文档评测任务',
  document_quality_reports: '文档质量报告', protected_versions: '受保护版本',
}
