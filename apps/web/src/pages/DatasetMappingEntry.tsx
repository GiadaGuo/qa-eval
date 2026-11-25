import { useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Space } from 'antd'

import { publishRawDatasetVersion, type Dataset, type DatasetVersion } from '../api/datasetVersions'
import MysqlDatasourcePage from './MysqlDatasourcePage'

export default function DatasetMappingEntry({ version, dataset, onPublished, onBack }: {
  version: DatasetVersion
  dataset: Dataset
  onPublished: (version: DatasetVersion) => void
  onBack: () => void
}) {
  const [legacy, setLegacy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const publishing = useRef(false)

  async function publish() {
    if (publishing.current) return
    publishing.current = true
    setBusy(true)
    setError('')
    try {
      await publishRawDatasetVersion(version.id)
      onPublished({ ...version, status: 'PUBLISHED', record_schema: '3.0', publication: null })
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '发布原始数据失败')
    } finally {
      publishing.current = false
      setBusy(false)
    }
  }

  if (legacy || version.raw_format !== 'jsonl') {
    return <MysqlDatasourcePage versionId={version.id} onBack={onBack} />
  }

  return <Card title={`${dataset.name} · 语义映射`} extra={<Button disabled={busy} onClick={onBack}>返回数据集</Button>}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Descriptions items={[
        { key: 'version', label: '数据版本', children: version.version },
        { key: 'status', label: '状态', children: '原始数据待发布' },
        { key: 'rows', label: '记录数', children: version.record_count?.toLocaleString('zh-CN') ?? '—' },
      ]} />
      <Alert type="info" showIcon title="发布原始数据后即可配置语义映射"
        description="发布将校验现有 JSONL 并构建样本索引。映射草稿和映射修订可在数据发布后独立编辑。" />
      {error && <Alert type="error" showIcon title={error} />}
      <Space wrap>
        <Button type="primary" loading={busy} onClick={() => void publish()}>发布原始数据（v3）</Button>
        <Button disabled={busy} onClick={() => setLegacy(true)}>使用 v2 字段映射</Button>
      </Space>
    </Space>
  </Card>
}
