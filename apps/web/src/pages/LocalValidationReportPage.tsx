/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 1：LOCAL_VALIDATION 真实质量报告展示页。
 * 数据来自后端只读加载的本地 canonical 产物，不冒充正式平台运行。
 */

import { useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Card,
  Descriptions,
  List,
  Pagination,
  Radio,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  fetchLocalValidationIssues,
  fetchLocalValidationManifest,
  fetchLocalValidationReport,
  fetchLocalValidationSummary,
  type DatasetManifest,
  type LocalValidationSummary,
  type QualityIssue,
  type QualityReport,
  type ReportMetricSection,
} from '../api/localValidation'

const { Title, Text } = Typography

const ISSUE_PAGE_SIZE = 20

type DeterminationFilter = 'ALL' | 'CONFIRMED' | 'CANDIDATE'

function formatRatio(value: number | string | null): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number' && value <= 1 && value >= 0) {
    return `${(value * 100).toFixed(4)}%`
  }
  return String(value)
}

function gateColor(status: string): 'success' | 'error' | 'warning' | 'default' {
  if (status === 'PASS') return 'success'
  if (status === 'FAIL') return 'error'
  return 'warning'
}

function executionColor(status: string): 'success' | 'error' | 'warning' | 'default' {
  if (status === 'SUCCEEDED') return 'success'
  if (status === 'FAILED') return 'error'
  if (['RUNNING', 'PENDING'].includes(status)) return 'warning'
  return 'default'
}

function severityColor(severity: string): string {
  const map: Record<string, string> = {
    INFO: 'blue',
    WARNING: 'orange',
    ERROR: 'red',
    CRITICAL: 'magenta',
  }
  return map[severity] ?? 'default'
}

function determinationColor(determination: string): 'success' | 'warning' | 'default' {
  if (determination === 'CONFIRMED') return 'success'
  if (determination === 'CANDIDATE') return 'warning'
  return 'default'
}

function renderSubject(issue: QualityIssue): string {
  const subject = issue.subject
  switch (subject.type) {
    case 'SAMPLE':
      return `样本 ${subject.sample_id}`
    case 'GROUP':
      return `重复组 ${subject.group_id}（${subject.member_count} 条）`
    case 'PAIR':
      return `样本对 ${subject.left_sample_id} ↔ ${subject.right_sample_id}`
    case 'CONTENT':
      return `样本 ${subject.sample_id} / 内容 ${subject.content_id}`
    case 'ANNOTATION':
      return `样本 ${subject.sample_id} / 标注 ${subject.annotation_id}`
    case 'DATASET':
      return '数据集整体'
    default:
      return JSON.stringify(subject)
  }
}

export default function LocalValidationReportPage() {
  const [summary, setSummary] = useState<LocalValidationSummary | null>(null)
  const [report, setReport] = useState<QualityReport | null>(null)
  const [manifest, setManifest] = useState<DatasetManifest | null>(null)
  const [issues, setIssues] = useState<QualityIssue[]>([])
  const [issueTotal, setIssueTotal] = useState(0)
  const [issuePage, setIssuePage] = useState(1)
  const [determination, setDetermination] = useState<DeterminationFilter>('ALL')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    Promise.all([
      fetchLocalValidationSummary(),
      fetchLocalValidationReport(),
      fetchLocalValidationManifest(),
      fetchLocalValidationIssues({ limit: ISSUE_PAGE_SIZE, offset: 0 }),
    ])
      .then(([summaryData, reportData, manifestData, issuesData]) => {
        if (cancelled) return
        setSummary(summaryData)
        setReport(reportData.report)
        setManifest(manifestData.manifest)
        setIssues(issuesData.issues)
        setIssueTotal(issuesData.total)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '未知错误')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [])

  const loadIssuePage = (page: number, filter: DeterminationFilter) => {
    setLoading(true)
    const query: { limit: number; offset: number; determination?: 'CONFIRMED' | 'CANDIDATE' } = {
      limit: ISSUE_PAGE_SIZE,
      offset: (page - 1) * ISSUE_PAGE_SIZE,
    }
    if (filter !== 'ALL') {
      query.determination = filter
    }
    fetchLocalValidationIssues(query)
      .then(data => {
        setIssues(data.issues)
        setIssueTotal(data.total)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '未知错误')
      })
      .finally(() => {
        setLoading(false)
      })
  }

  const handlePageChange = (page: number) => {
    setIssuePage(page)
    loadIssuePage(page, determination)
  }

  const handleDeterminationChange = (value: DeterminationFilter) => {
    setDetermination(value)
    setIssuePage(1)
    loadIssuePage(1, value)
  }

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
        <div style={{ marginTop: 16 }}><Text type="secondary">加载 LOCAL_VALIDATION 报告…</Text></div>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" showIcon title="报告加载失败" description={error} />
      </div>
    )
  }

  const metricSection: ReportMetricSection | undefined = report?.metrics[0]
  const observations = metricSection?.observations ?? []
  const ruleEvaluations = metricSection?.rule_evaluations ?? []

  const observationColumns: TableColumnsType<{ key: string; value: number | string; unit: string | null }> = [
    { title: '观测指标', dataIndex: 'key', key: 'key' },
    {
      title: '数值',
      dataIndex: 'value',
      key: 'value',
      render: (value: number | string, record) => (
        <Text strong>{record.unit === 'ratio' ? formatRatio(value) : String(value)}</Text>
      ),
    },
    { title: '单位', dataIndex: 'unit', key: 'unit', render: (unit: string | null) => unit ?? '—' },
  ]

  return (
    <div style={{ width: '100%', maxWidth: 1440, minWidth: 0, margin: '0 auto', padding: 24, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <Title level={2}>质量报告 · LOCAL_VALIDATION</Title>
          <Text type="secondary">
            本页展示的是本地已跑通的真实 canonical 产物，用于闭环 1 验证；
            未被登记为平台正式 EvaluationRun。
          </Text>
        </div>

        <Alert
          type="warning"
          showIcon
          message="LOCAL_VALIDATION 标识"
          description={
            <Space direction="vertical" size={0}>
              <Text>authority：{summary?.authority}</Text>
              <Text>not_a_platform_run：{summary?.not_a_platform_run ? 'true' : 'false'}</Text>
              <Text>产物目录：{summary?.run_dir}</Text>
            </Space>
          }
        />

        <Card title="数据集版本">
          <Descriptions bordered column={{ xs: 1, sm: 2, md: 3 }}>
            <Descriptions.Item label="数据集名称">{manifest?.name ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Dataset Version ID">{manifest?.dataset_version_id ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="模态">{manifest?.modalities.join(', ') ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="来源">{manifest?.source_snapshot.source_uri ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="源记录数">{manifest?.source_snapshot.record_count ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="标准化记录数">
              {manifest?.artifacts.find(a => a.kind === 'NORMALIZED_DATA')?.record_count ?? '—'}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        <Card title="报告概览">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 16 }}>
            <Statistic
              title="门禁状态"
              value={report?.gate_status ?? '—'}
              valueStyle={{ color: gateColor(report?.gate_status ?? '').replace('success', '#52c41a').replace('error', '#ff4d4f').replace('warning', '#faad14') }}
            />
            <Statistic
              title="执行状态"
              value={report?.execution_status ?? '—'}
              valueStyle={{ color: executionColor(report?.execution_status ?? '').replace('success', '#52c41a').replace('error', '#ff4d4f').replace('warning', '#faad14') }}
            />
            <Statistic title="已评测指标" value={report?.coverage.evaluated ?? 0} />
            <Statistic title="不适用" value={report?.coverage.not_applicable ?? 0} />
            <Statistic title="缺失" value={report?.coverage.missing ?? 0} />
            <Statistic title="错误" value={report?.coverage.error ?? 0} />
            <Statistic title="未评测" value={report?.coverage.not_evaluated ?? 0} />
          </div>
        </Card>

        <Card title={`指标：${metricSection?.metric.name ?? '—'} (${metricSection?.metric.key ?? '—'})`}>
          <Table
            rowKey="key"
            columns={observationColumns}
            dataSource={observations}
            pagination={false}
            size="small"
          />
          <div style={{ marginTop: 16 }}>
            <Text strong>规则判定：</Text>
            <Space wrap>
              {ruleEvaluations.map(rule => (
                <Tag key={rule.rule_evaluation_id} color={rule.status === 'PASS' ? 'success' : rule.status === 'FAIL' ? 'error' : 'warning'}>
                  {rule.contract_item_key} · {rule.status}
                </Tag>
              ))}
            </Space>
          </div>
        </Card>

        <Card title="问题汇总">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 16 }}>
            <Badge count={report?.issue_summary.confirmed_count ?? 0} showZero color="red">
              <Card size="small">CONFIRMED</Card>
            </Badge>
            <Badge count={report?.issue_summary.candidate_count ?? 0} showZero color="orange">
              <Card size="small">CANDIDATE</Card>
            </Badge>
            <Badge count={report?.issue_summary.open_count ?? 0} showZero color="blue">
              <Card size="small">OPEN</Card>
            </Badge>
          </div>
          {metricSection?.issue_summary.by_code && (
            <div style={{ marginTop: 16 }}>
              <Text strong>按 code 分布：</Text>
              <Space wrap>
                {Object.entries(metricSection.issue_summary.by_code).map(([code, count]) => (
                  <Tag key={code}>{code}: {count}</Tag>
                ))}
              </Space>
            </div>
          )}
        </Card>

        <Card
          title="问题下钻"
          extra={
            <Radio.Group
              value={determination}
              onChange={event => handleDeterminationChange(event.target.value as DeterminationFilter)}
            >
              <Radio.Button value="ALL">全部 ({report?.issue_summary.open_count ?? 0})</Radio.Button>
              <Radio.Button value="CONFIRMED">CONFIRMED ({report?.issue_summary.confirmed_count ?? 0})</Radio.Button>
              <Radio.Button value="CANDIDATE">CANDIDATE ({report?.issue_summary.candidate_count ?? 0})</Radio.Button>
            </Radio.Group>
          }
        >
          <List
            dataSource={issues}
            renderItem={issue => (
              <List.Item>
                <div style={{ width: '100%' }}>
                  <Space wrap>
                    <Tag color={determinationColor(issue.determination)}>{issue.determination}</Tag>
                    <Tag color={severityColor(issue.severity)}>{issue.severity}</Tag>
                    <Tag>{issue.code}</Tag>
                    <Tag>{issue.scope}</Tag>
                  </Space>
                  <div style={{ marginTop: 8 }}>
                    <Text strong>{renderSubject(issue)}</Text>
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary">{issue.message}</Text>
                  </div>
                  {issue.related_subjects.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary">关联：</Text>
                      <Space wrap>
                        {issue.related_subjects.map((related, index) => (
                          <Tag key={index}>
                            {related.relation}: {'sample_id' in related.subject ? related.subject.sample_id : JSON.stringify(related.subject)}
                          </Tag>
                        ))}
                      </Space>
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      issue_id: {issue.issue_id} · detector: {issue.detector.key}@{issue.detector.version}
                    </Text>
                  </div>
                </div>
              </List.Item>
            )}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <Pagination
              current={issuePage}
              pageSize={ISSUE_PAGE_SIZE}
              total={issueTotal}
              showSizeChanger={false}
              onChange={handlePageChange}
            />
          </div>
        </Card>
      </div>
    </div>
  )
}
