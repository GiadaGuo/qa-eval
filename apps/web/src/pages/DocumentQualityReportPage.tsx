/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-28
 * @Description:
 * P3：说明文档质量评测报告详情页。展示总分、四指标卡片、
 * 检查项列表、缺失项与建议。
 */

import { useEffect, useState } from 'react'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  List,
  Space,
  Spin,
  Statistic,
  Tag,
  Timeline,
  Typography,
} from 'antd'

import {
  getDocumentQualityReport,
  type DocumentItemSection,
  type DocumentMetricSection,
  type DocumentQualityReport,
} from '../api/documentReportTasks'

const { Title, Text } = Typography

interface DocumentQualityReportPageProps {
  reportId: string
  onBack: () => void
}

const statusConfig: Record<
  DocumentMetricSection['status'],
  { color: string; icon: React.ReactNode; text: string }
> = {
  OK: { color: 'success', icon: <CheckCircleOutlined />, text: '正常' },
  ERROR: { color: 'error', icon: <CloseCircleOutlined />, text: 'ERROR' },
}

const itemStatusConfig: Record<
  DocumentItemSection['status'],
  { color: string; icon: React.ReactNode; text: string }
> = {
  SATISFIED: { color: 'success', icon: <CheckCircleOutlined />, text: '满足' },
  INSUFFICIENT: { color: 'warning', icon: <WarningOutlined />, text: '不足' },
  MISSING: { color: 'error', icon: <CloseCircleOutlined />, text: '缺失' },
  UNVERIFIABLE: { color: 'default', icon: <InfoCircleOutlined />, text: '不可核实' },
  ERROR: { color: 'error', icon: <ExclamationCircleOutlined />, text: '错误' },
}

const fixabilityText: Record<DocumentItemSection['fixability'], string> = {
  AUTO_FIXABLE: '可自动修复',
  DATA_DERIVABLE: '可从数据派生',
  REQUIRES_HUMAN_INPUT: '需人工补充',
}

export default function DocumentQualityReportPage({
  reportId,
  onBack,
}: DocumentQualityReportPageProps) {
  const [report, setReport] = useState<DocumentQualityReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getDocumentQualityReport(reportId)
      .then((data) => {
        if (!cancelled) setReport(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reportId])

  if (loading) return <Spin tip="加载报告中..." style={{ display: 'block', marginTop: 48 }} />
  if (error)
    return (
      <Alert
        type="error"
        message="报告加载失败"
        description={error}
        action={<Button onClick={onBack}>返回</Button>}
      />
    )
  if (!report) return <Empty description="报告不存在" />

  const satisfiedCount = report.metrics
    .filter((m) => m.status === 'OK')
    .reduce(
      (sum, m) =>
        sum + m.items.filter((item) => item.status === 'SATISFIED').length,
      0,
    )
  const totalItems = report.metrics.reduce((sum, m) => sum + m.items.length, 0)

  return (
    <Card
      title={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
            返回
          </Button>
          <Title level={4} style={{ margin: 0 }}>
            说明文档质量报告
          </Title>
        </Space>
      }
    >
      <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 3 }} style={{ marginBottom: 24 }}>
        <Descriptions.Item label="报告 ID">{report.report_id}</Descriptions.Item>
        <Descriptions.Item label="文档版本">{report.document.version}</Descriptions.Item>
        <Descriptions.Item label="文件名">{report.document.filename}</Descriptions.Item>
        <Descriptions.Item label="文档行数">{report.document.line_count}</Descriptions.Item>
        <Descriptions.Item label="评测标准">
          {report.evaluation_standard.name} v{report.evaluation_standard.version}
        </Descriptions.Item>
        <Descriptions.Item label="评测模型">{report.evaluator.model}</Descriptions.Item>
        <Descriptions.Item label="生成时间">
          {new Date(report.generated_at).toLocaleString()}
        </Descriptions.Item>
        <Descriptions.Item label="执行状态">
          <Badge
            status={
              report.execution_status === 'SUCCEEDED'
                ? 'success'
                : report.execution_status === 'FAILED'
                  ? 'error'
                  : 'warning'
            }
            text={
              report.execution_status === 'SUCCEEDED'
                ? '全部完成'
                : report.execution_status === 'PARTIAL_SUCCEEDED'
                  ? '部分完成'
                  : '失败'
            }
          />
        </Descriptions.Item>
        <Descriptions.Item label="满足项 / 总项">
          {satisfiedCount} / {totalItems}
        </Descriptions.Item>
      </Descriptions>

      <Card type="inner" title="总体得分" style={{ marginBottom: 24 }}>
        <Space size="large" align="start">
          <Statistic
            title={report.overall.scoring_policy}
            value={report.overall.score ?? '—'}
            suffix={report.overall.score !== null ? '分' : undefined}
            valueStyle={{
              color:
                report.overall.score === null
                  ? '#999'
                  : report.overall.score >= 80
                    ? '#389e0d'
                    : report.overall.score >= 60
                      ? '#d48806'
                      : '#cf1322',
            }}
          />
          {report.overall.explanation && (
            <Alert type="info" showIcon message={report.overall.explanation} />
          )}
        </Space>
      </Card>

      {report.limitations.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="评测限制"
          description={
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {report.limitations.map((limitation, index) => (
                <li key={index}>{limitation}</li>
              ))}
            </ul>
          }
          style={{ marginBottom: 24 }}
        />
      )}

      <Title level={5}>四维度评测结果</Title>
      <Collapse defaultActiveKey={report.metrics.map((m) => m.metric.key)} style={{ marginBottom: 24 }}>
        {report.metrics.map((metric) => {
          const scoreObservation = metric.observations.find((o) => o.key === 'score')
          const satisfiedObservation = metric.observations.find((o) => o.key === 'satisfied_count')
          const requiredObservation = metric.observations.find((o) => o.key === 'required_count')
          return (
            <Collapse.Panel
              key={metric.metric.key}
              header={
                <Space>
                  <Tag color={statusConfig[metric.status].color}>
                    {statusConfig[metric.status].icon} {statusConfig[metric.status].text}
                  </Tag>
                  <Text strong>
                    {metric.metric.key} {metric.metric_name}
                  </Text>
                  {metric.status === 'OK' && (
                    <Text type="secondary">
                      {String(satisfiedObservation?.value ?? '-')}/{String(requiredObservation?.value ?? '-')} 项满足
                      {scoreObservation && ` · ${String(scoreObservation.value)} 分`}
                    </Text>
                  )}
                  {metric.status === 'ERROR' && metric.error && (
                    <Text type="danger">{metric.error}</Text>
                  )}
                </Space>
              }
            >
              {metric.status === 'OK' ? (
                <List
                  size="small"
                  dataSource={metric.items}
                  renderItem={(item) => (
                    <List.Item>
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Space>
                          <Tag color={itemStatusConfig[item.status].color}>
                            {itemStatusConfig[item.status].icon}{' '}
                            {itemStatusConfig[item.status].text}
                          </Tag>
                          <Text strong>
                            {item.item_code} {item.item_name}
                          </Text>
                          <Tag>{fixabilityText[item.fixability]}</Tag>
                        </Space>
                        <Text type="secondary">检查范围：{item.inspected_scope}</Text>
                        <Text>判定理由：{item.reason}</Text>
                        <Text>修改建议：{item.suggestion}</Text>
                        {item.document_evidence.length > 0 && (
                          <Timeline
                            items={item.document_evidence.map((evidence, index) => ({
                              key: index,
                              children: (
                                <div>
                                  <Text type="secondary">
                                    第 {evidence.line_start}
                                    {evidence.line_end !== evidence.line_start &&
                                      `–${evidence.line_end}`}
                                    行
                                  </Text>
                                  <blockquote
                                    style={{
                                      margin: '4px 0 0 0',
                                      padding: '4px 8px',
                                      borderLeft: '3px solid #6F9E88',
                                      background: '#F7FAF7',
                                    }}
                                  >
                                    {evidence.excerpt}
                                  </blockquote>
                                </div>
                              ),
                            }))}
                          />
                        )}
                        {item.status === 'MISSING' && item.expected_section && (
                          <Text type="warning">建议插入位置：{item.expected_section}</Text>
                        )}
                      </Space>
                    </List.Item>
                  )}
                />
              ) : (
                <Alert type="error" message={metric.error ?? '该指标评测失败'} />
              )}
            </Collapse.Panel>
          )
        })}
      </Collapse>

      {report.missing_items.length > 0 && (
        <Card type="inner" title="缺失项汇总" style={{ marginBottom: 24 }}>
          <List
            size="small"
            dataSource={report.missing_items}
            renderItem={(item) => (
              <List.Item>
                <Text>
                  <Text strong>{item.item_code}</Text> {item.item_name}
                  <Text type="secondary"> — 建议插入：{item.expected_section}</Text>
                </Text>
              </List.Item>
            )}
          />
        </Card>
      )}

      {report.recommendations.length > 0 && (
        <Card type="inner" title="修改建议">
          <List
            size="small"
            dataSource={report.recommendations}
            renderItem={(item) => (
              <List.Item>
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Text strong>{item.item_code}</Text>
                  <Text>{item.suggestion}</Text>
                  <Tag>{fixabilityText[item.fixability as DocumentItemSection['fixability']]}</Tag>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
    </Card>
  )
}
