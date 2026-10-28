/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：数据集列表与创建页面。
 */

import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, List, Modal, Popconfirm, Select, Space, Tag, Typography, message } from 'antd'

import { createDataset, deleteDataset, listDatasets, type Dataset, type DatasetCreate } from '../api/datasetVersions'

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

  const handleDelete = async (dataset: Dataset) => {
    setDeletingId(dataset.id)
    try {
      await deleteDataset(dataset.id)
      const data = await listDatasets()
      setDatasets(data)
      message.success(`数据集「${dataset.name}」已删除`)
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
                <Popconfirm
                  key="delete"
                  title={`删除数据集「${dataset.name}」？`}
                  description="将同时删除该数据集下的全部版本；已被评测任务引用的数据集无法删除。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={() => handleDelete(dataset)}
                  disabled={deletingId !== null}
                >
                  <Button
                    danger
                    type="link"
                    loading={deletingId === dataset.id}
                    disabled={deletingId !== null && deletingId !== dataset.id}
                  >
                    删除
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={<span className="dataset-name">{dataset.name}</span>}
                description={
                  <Space align="start" wrap>
                    <Tag color="blue">{dataset.modality.join(', ')}</Tag>
                    {dataset.description && <span className="dataset-description">{dataset.description}</span>}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

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
              <Option value="text">text</Option>
              <Option value="image">image</Option>
              <Option value="audio">audio</Option>
              <Option value="video">video</Option>
              <Option value="structured">structured</Option>
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
