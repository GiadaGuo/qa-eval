/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：Dataset Version 详情页：Manifest、Sample 预览、Profile。
 */

import { useEffect, useState } from 'react'
import { ArrowLeftOutlined, RobotOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  Descriptions,
  List,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'

import {
  getDatasetVersion,
  getManifest,
  getProfile,
  getSamples,
  type Dataset,
  type DatasetManifest,
  type DatasetProfile,
  type DatasetVersion,
  type PaginatedSamples,
} from '../api/datasetVersions'
import {
  datasetVersionStatusLabel,
  formatModalityList,
  mappingActionLabel,
  mappingTargetKindLabel,
  modalityLabel,
  presenceLabel,
  presencePolicyLabel,
} from '../utils/displayLabels'

const { Title, Text } = Typography

interface DatasetVersionPageProps {
  dataset: Dataset
  version: DatasetVersion
  onBack: () => void
  onOpenAgentWorkspace: () => void
}

export default function DatasetVersionPage({ dataset, version, onBack, onOpenAgentWorkspace }: DatasetVersionPageProps) {
  const [versionDetail, setVersionDetail] = useState<DatasetVersion | null>(null)
  const [manifest, setManifest] = useState<DatasetManifest | null>(null)
  const [samples, setSamples] = useState<PaginatedSamples | null>(null)
  const [profile, setProfile] = useState<DatasetProfile | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({
    version: true,
    manifest: version.status === 'PUBLISHED',
    samples: version.status === 'PUBLISHED',
    profile: version.status === 'PUBLISHED',
  })

  useEffect(() => {
    let cancelled = false
    getDatasetVersion(version.id)
      .then((data) => {
        if (!cancelled) setVersionDetail(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '版本加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading((prev) => ({ ...prev, version: false }))
      })
    if (version.status !== 'PUBLISHED') {
      return () => { cancelled = true }
    }
    getManifest(version.id)
      .then((data) => {
        if (!cancelled) setManifest(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : 'Manifest 加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading((prev) => ({ ...prev, manifest: false }))
      })
    getProfile(version.id)
      .then((data) => {
        if (!cancelled) setProfile(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '数据画像加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading((prev) => ({ ...prev, profile: false }))
      })
    return () => {
      cancelled = true
    }
  }, [version.id, version.status])

  useEffect(() => {
    let cancelled = false
    if (version.status !== 'PUBLISHED') {
      return () => { cancelled = true }
    }
    getSamples(version.id, 50)
      .then((data) => {
        if (!cancelled) setSamples(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '样本加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading((prev) => ({ ...prev, samples: false }))
      })
    return () => {
      cancelled = true
    }
  }, [version.id, version.status])

  const unpublishedHint = <Text type="secondary">版本发布后生成并展示该产物。</Text>

  const manifestArtifactsColumns = [
    { title: '类型', dataIndex: 'kind', key: 'kind' },
    { title: '格式', dataIndex: 'format', key: 'format' },
    { title: '记录数', dataIndex: 'record_count', key: 'record_count' },
    { title: '大小（字节）', dataIndex: 'size_bytes', key: 'size_bytes' },
    {
      title: 'URI',
      dataIndex: 'uri',
      key: 'uri',
      render: (uri: string) => <Text ellipsis style={{ maxWidth: 240 }} title={uri}>{uri}</Text>,
    },
  ]

  const sampleColumns = [
    { title: '样本 ID', dataIndex: 'sample_id', key: 'sample_id' },
    {
      title: '内容',
      key: 'content',
      render: (_: unknown, record: { contents: Array<{ presence: string; payload?: { text?: string } | null }> }) => {
        const text = record.contents.map((c) => c.payload?.text ?? `（${presenceLabel(c.presence)}）`).join(' | ')
        return <Text ellipsis style={{ maxWidth: 400 }} title={text}>{text}</Text>
      },
    },
    {
      title: '来源行号',
      dataIndex: 'source',
      key: 'source_row',
      render: (source: { source_row: number | null }) => source.source_row ?? '-',
    },
  ]

  const fieldProfileColumns = [
    { title: '字段', dataIndex: 'field', key: 'field' },
    { title: '模态', dataIndex: 'modality', key: 'modality', render: (value: string) => modalityLabel(value) },
    { title: '存在数', key: 'present_count', render: (_: unknown, record: { present_count?: number; non_null_count?: number }) => record.present_count ?? record.non_null_count ?? 0 },
    { title: '空值数', dataIndex: 'null_count', key: 'null_count' },
    { title: '缺失数', dataIndex: 'missing_count', key: 'missing_count', render: (value: number | undefined) => value ?? 0 },
    { title: '空字符串数', dataIndex: 'empty_count', key: 'empty_count', render: (value: number | undefined) => value ?? 0 },
    { title: '总数', dataIndex: 'total_count', key: 'total_count' },
    {
      title: '长度统计',
      key: 'length',
      render: (_: unknown, record: { content_length?: { min: number; max: number; mean: number; median: number } }) => {
        if (!record.content_length) return '-'
        const { min, max, mean, median } = record.content_length
        return `最小 ${min} · 最大 ${max} · 平均 ${mean.toFixed(1)} · 中位数 ${median}`
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
            {dataset.name} / {version.version}
          </Title>
          <Tag color={version.status === 'PUBLISHED' ? 'success' : 'default'}>
            {datasetVersionStatusLabel(version.status)}
          </Tag>
          <Button type="primary" icon={<RobotOutlined />} disabled={version.status !== 'PUBLISHED' || loading.profile || !profile} onClick={onOpenAgentWorkspace}>
            数据集综合质量评测
          </Button>
        </Space>
      }
    >
      <Tabs
        items={[
          {
            key: 'overview',
            label: '概览',
            children: (
              <Spin spinning={loading.version}>
                {versionDetail && (
                  <Descriptions bordered column={2}>
                    <Descriptions.Item label="版本 ID">{versionDetail.id}</Descriptions.Item>
                    <Descriptions.Item label="数据集 ID">{versionDetail.dataset_id}</Descriptions.Item>
                    <Descriptions.Item label="版本号">{versionDetail.version}</Descriptions.Item>
                    <Descriptions.Item label="状态">{datasetVersionStatusLabel(versionDetail.status)}</Descriptions.Item>
                    <Descriptions.Item label="创建时间">
                      {versionDetail.created_at ? new Date(versionDetail.created_at).toLocaleString() : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="发布时间">
                      {versionDetail.published_at ? new Date(versionDetail.published_at).toLocaleString() : '-'}
                    </Descriptions.Item>
                  </Descriptions>
                )}
              </Spin>
            ),
          },
          {
            key: 'manifest',
            label: '数据清单（Manifest）',
            children: (
              <Spin spinning={loading.manifest}>
                {version.status !== 'PUBLISHED' ? unpublishedHint : manifest && (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Descriptions bordered column={2}>
                      <Descriptions.Item label="Schema 版本">{manifest.schema_version}</Descriptions.Item>
                      <Descriptions.Item label="名称">{manifest.name}</Descriptions.Item>
                      <Descriptions.Item label="模态">{formatModalityList(manifest.modalities)}</Descriptions.Item>
                      <Descriptions.Item label="来源类型">{manifest.source_snapshot.source_type}</Descriptions.Item>
                      <Descriptions.Item label="记录数">{manifest.source_snapshot.record_count}</Descriptions.Item>
                      <Descriptions.Item label="采集时间">
                        {new Date(manifest.source_snapshot.captured_at).toLocaleString()}
                      </Descriptions.Item>
                    </Descriptions>
                    <Title level={5}>产物文件</Title>
                    <Table
                      rowKey="artifact_id"
                      dataSource={manifest.artifacts}
                      columns={manifestArtifactsColumns}
                      pagination={false}
                    />
                    <Title level={5}>字段映射</Title>
                    <List
                      dataSource={manifest.field_mapping.mappings}
                      renderItem={(mapping) => (
                        <List.Item>
                          <Text>
                            {mapping.source_path} → {mapping.target ? mappingTargetKindLabel(mapping.target.kind) : mappingActionLabel(mapping.action)}
                            {mapping.target?.role ? `（${mapping.target.role}）` : ''}
                            {' '}
                            ［{presencePolicyLabel(mapping.presence_policy)}］
                          </Text>
                        </List.Item>
                      )}
                    />
                  </Space>
                )}
              </Spin>
            ),
          },
          {
            key: 'samples',
            label: '标准样本预览',
            children: (
              <Spin spinning={loading.samples}>
                {version.status !== 'PUBLISHED' ? unpublishedHint : samples && (
                  <>
                    <Table
                      rowKey="sample_id"
                      dataSource={samples.samples}
                      columns={sampleColumns}
                      pagination={{
                        pageSize: 5,
                        total: samples.samples.length,
                        showSizeChanger: false,
                        showTotal: (total, range) =>
                          `预览第 ${range[0]}-${range[1]} 条，共加载 ${total} 条`,
                      }}
                    />
                    <Text type="secondary">
                      当前仅加载前 {samples.samples.length} 条预览，数据集共 {samples.total} 条标准样本
                    </Text>
                  </>
                )}
              </Spin>
            ),
          },
          {
            key: 'profile',
            label: '数据画像',
            children: (
              <Spin spinning={loading.profile}>
                {version.status !== 'PUBLISHED' ? unpublishedHint : profile && (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Descriptions bordered column={3}>
                      <Descriptions.Item label="原始记录数">
                        {profile.summary.source_record_count}
                      </Descriptions.Item>
                      <Descriptions.Item label="标准化记录数">
                        {profile.summary.normalized_record_count}
                      </Descriptions.Item>
                      <Descriptions.Item label="拒绝记录数">
                        {profile.summary.rejected_record_count}
                      </Descriptions.Item>
                    </Descriptions>
                    {profile.identity && profile.structure && (
                      <Descriptions bordered column={3}>
                        <Descriptions.Item label="样本数">{profile.identity.sample_count}</Descriptions.Item>
                        <Descriptions.Item label="格式">{profile.identity.format}</Descriptions.Item>
                        <Descriptions.Item label="大小">{profile.identity.size_bytes} 字节</Descriptions.Item>
                        <Descriptions.Item label="模态">{formatModalityList(profile.structure.modalities)}</Descriptions.Item>
                        <Descriptions.Item label="内容角色" span={2}>{profile.structure.content_roles.join('、')}</Descriptions.Item>
                      </Descriptions>
                    )}
                    {profile.statistics && (
                      <Descriptions bordered column={3}>
                        <Descriptions.Item label="长度均值">{String(profile.statistics.text_length.mean ?? '—')}</Descriptions.Item>
                        <Descriptions.Item label="P50">{String(profile.statistics.text_length.p50 ?? '—')}</Descriptions.Item>
                        <Descriptions.Item label="P95">{String(profile.statistics.text_length.p95 ?? '—')}</Descriptions.Item>
                        <Descriptions.Item label="语言分布" span={3}>{JSON.stringify(profile.content_features?.language_distribution ?? {})}（{profile.content_features?.language_source ?? '未知'}）</Descriptions.Item>
                      </Descriptions>
                    )}
                    <Title level={5}>字段画像</Title>
                    <Table
                      rowKey="field"
                      dataSource={profile.field_profiles}
                      columns={fieldProfileColumns}
                      pagination={false}
                    />
                  </Space>
                )}
              </Spin>
            ),
          },
        ]}
      />
    </Card>
  )
}
