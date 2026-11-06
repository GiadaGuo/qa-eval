/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：数据集列表与创建页面。
 */

import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Form, Input, List, Modal, Select, Space, Spin, Tag, Typography, message } from 'antd'

import { createDataset, deleteDataset, getDatasetDeletionPreview, listDatasets, type Dataset, type DatasetCreate, type DatasetDeletionPreview } from '../api/datasetVersions'
import { formatModalityList } from '../utils/displayLabels'

const { Title, Text } = Typography
const { Option } = Select

interface DatasetListPageProps {
  onSelectDataset: (dataset: Dataset) => void
}

export default function DatasetListPage({ onSelectDataset }: DatasetListPageProps) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Dataset | null>(null)
  const [deletePreview, setDeletePreview] = useState<DatasetDeletionPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [form] = Form.useForm<DatasetCreate>()

  useEffect(() => {
    let cancelled = false
    listDatasets()
      .then((data) => {
        if (!cancelled) setDatasets(data)
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
  }, [])

  const handleCreate = async (values: DatasetCreate) => {
    try {
      await createDataset(values)
      setModalOpen(false)
      form.resetFields()
      const data = await listDatasets()
      setDatasets(data)
      message.success('数据集创建成功')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
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
      const data = await listDatasets()
      setDatasets(data)
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
            <Text type="secondary">共 {datasets.length} 个</Text>
          </Space>
        }
        extra={
          <Button type="primary" onClick={() => setModalOpen(true)}>
            新建数据集
          </Button>
        }
      >
        <List
          className="dataset-list"
          loading={loading}
          dataSource={datasets}
          renderItem={(dataset) => (
            <List.Item
              actions={[
                <Button key="view" type="link" onClick={() => onSelectDataset(dataset)}>
                  查看版本
                </Button>,
                <Button
                  key="delete"
                  danger
                  type="link"
                  onClick={() => void openDeletePreview(dataset)}
                  loading={previewLoading && deleteTarget?.id === dataset.id}
                  disabled={deletingId !== null || previewLoading}
                >
                  删除
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={<span className="dataset-name">{dataset.name}</span>}
                description={
                  <Space align="start" wrap>
                    <Tag color="blue">{formatModalityList(dataset.modality)}</Tag>
                    {dataset.description && <span className="dataset-description">{dataset.description}</span>}
                  </Space>
                }
              />
            </List.Item>
          )}
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
        title="新建数据集"
        open={modalOpen}
        onOk={() => form.submit()}
        onCancel={() => setModalOpen(false)}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
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
            <Select mode="multiple" placeholder="请选择">
              <Option value="text">文本</Option>
              <Option value="image">图像</Option>
              <Option value="audio">音频</Option>
              <Option value="video">视频</Option>
              <Option value="structured">结构化</Option>
            </Select>
          </Form.Item>
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
