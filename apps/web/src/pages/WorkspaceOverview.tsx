import { CheckOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Drawer, Empty, Input, Select, Space, Statistic, Tag, Modal } from 'antd'
import { useNavigate } from 'react-router-dom'
import { accountRequest } from '../api/webSession'
import { workLabels, workPath, type Overview, type WorkItem } from '../api/workspace'
import { evaluationRunStatusLabel } from '../utils/evaluationRunStatus'

const stats = [
  ['datasets', '数据集数', '/app/datasets'],
  ['pending', '待完成评测数', '/app/evaluation?tab=tasks&status=PENDING'],
  ['running', '运行中任务数', '/app/evaluation?tab=tasks&status=RUNNING'],
  ['reports', '已生成报告数', '/app/evaluation?tab=results'],
] as const

export default function WorkspaceOverview() {
  const navigate = useNavigate()
  const [data, setData] = useState<Overview>()
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState('')
  useEffect(() => {
    let active = true
    const load = () => accountRequest<Overview>('workspace').then(
      value => { if (active) { setData(value); setError('') } },
      (failure: Error) => { if (active) setError(failure.message) },
    )
    void load()
    const timer = window.setInterval(load, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const recordUrl = (item: WorkItem) => 'workspace/checkpoints/' + item.kind + '/' + item.object_id
  async function act(item: WorkItem) {
    setBusy(recordUrl(item))
    setError('')
    try {
      if (item.valid) {
        const fresh = await accountRequest<Partial<WorkItem>>(recordUrl(item))
        navigate(workPath({ ...item, ...fresh }))
      } else {
        await accountRequest(recordUrl(item), 'DELETE')
        setData(await accountRequest<Overview>('workspace'))
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '工作记录操作失败')
    } finally { setBusy('') }
  }
  function flow(item: WorkItem) {
    const version = item.version_id ?? item.object_id
    const versionPath = '/app/datasets?rawVersion=' + version
    const evaluationPath = '/app/evaluation?version=' + version
    const runPath = '/app/evaluation?tab=tasks&run=' + item.run_id
    const reportPath = '/app/evaluation?tab=results&run=' + item.run_id + '&report=' + item.report_id
    const document = item.kind === 'document' || item.kind === 'document-report' || item.category === 'DOCUMENT' || item.category === 'COMBINED'
    const single = ['single', 'operator'].includes(item.kind) || item.category === 'SINGLE_METRIC'
    const current = workPath(item)
    const datasetPath = item.dataset_id ? '/app/datasets?dataset=' + item.dataset_id : undefined
    const documentPath = datasetPath && item.document_id ? datasetPath + '&document=' + item.document_id : undefined
    const readyForContract = item.status === 'PUBLISHED' && ['version', 'ingestion', 'mapping'].includes(item.kind)
    const contractPath = item.contract_id ? evaluationPath + '&mode=manual&contract=' + item.contract_id + (item.run_id ? '&run=' + item.run_id : '') : undefined
    const steps: Array<[string, string | undefined]> = document || single
      ? [
        [single ? '配置算子' : '配置文档评测', ['single', 'operator', 'document'].includes(item.kind) ? current : single && item.version_id ? versionPath + '&view=single' : documentPath],
        ['执行评测', item.run_id ? runPath : undefined],
        ['查看报告', item.report_id ? reportPath : item.kind === 'document-report' ? current : undefined],
      ]
      : [
        ['数据接入', item.kind === 'ingestion' ? current : datasetPath],
        ['版本发布', item.kind === 'mapping' ? current : item.version_id ? versionPath : undefined],
        ['质量契约', item.kind === 'contract' || item.kind === 'agent' || item.kind === 'manual' ? current : item.version_id && contractPath ? contractPath : readyForContract ? evaluationPath : undefined],
        ['执行评测', item.run_id ? runPath : undefined],
        ['查看报告', item.kind === 'issue' ? current : item.report_id ? reportPath : undefined],
      ]
    const stage = document || single
      ? item.report_id || item.kind === 'document-report' ? 2 : item.run_id ? 1 : 0
      : item.report_id ? 4 : item.run_id ? 3 : (readyForContract || ['agent', 'manual', 'contract'].includes(item.kind)) ? 2 : item.kind === 'dataset' || item.kind === 'ingestion' ? 0 : 1
    return { steps, stage }
  }
  function progress(item: WorkItem) {
    const { steps, stage } = flow(item)
    return <Space wrap className="qa-work-steps" aria-label="流程进度">
      {steps.map(([label, path], index) => <Button key={label} size="small" type={index === stage ? 'primary' : index > stage ? 'dashed' : 'default'}
        aria-current={index === stage ? 'step' : undefined} icon={path && index < stage ? <CheckOutlined /> : undefined}
        onClick={() => {
          if (path && index <= stage) {
            void accountRequest<Partial<WorkItem>>(recordUrl(item)).then(fresh => {
              const current = flow({ ...item, ...fresh })
              const target = current.steps[index]?.[1]
              if (target && index <= current.stage) navigate(target)
              else setError('工作进度已变更，请刷新后重试')
            }, failure => setError(failure.message))
          } else Modal.info({
            title: path || index > stage ? '请先完成前置操作' : '此记录没有对应产物',
            content: index > stage ? '请先继续当前工作，完成后即可进入' + label + '。' : '可从当前工作查看已关联的业务对象。',
          })
        }}>{label}</Button>)}
    </Space>
  }
  function card(item: WorkItem, featured = false) {
    return <div key={recordUrl(item)} className={featured ? 'qa-work-featured qa-work-row' : 'qa-work-row'}>
      <div>
        <strong>{item.name ?? '已失效的工作对象'}</strong>{item.version && <span> · {item.version}</span>}
        <div className="qa-work-meta">{workLabels[item.kind]} · {new Date(item.touched_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</div>
      </div>
      <Space wrap>
        <Tag>{item.valid ? ({ PUBLISHED: '已发布', DRAFT: '草稿版本', ACTIVE: '进行中' }[item.status ?? ''] ?? evaluationRunStatusLabel(item.status ?? '进行中')) : '已失效'}</Tag>
        <Button type={featured ? 'primary' : 'default'} loading={busy === recordUrl(item)} onClick={() => void act(item)}>
          {item.valid ? '继续工作' : '隐藏记录'}
        </Button>
      </Space>
      {featured && item.valid && progress(item)}
    </div>
  }
  const records = data?.items.filter(item =>
    (!filter || (filter === 'invalid' ? !item.valid : item.kind === filter)) &&
    [item.name, item.version, item.object_id, workLabels[item.kind]].some(value => value?.toLowerCase().includes(search.trim().toLowerCase())),
  ) ?? []
  return <>
    {error && <Alert type="error" showIcon title={error} />}
    <Card title="工作概览" loading={!data && !error}>
      <div className="qa-work-stats">
        {stats.map(([key, label, path]) => <button className="qa-shortcut" key={key} onClick={() => navigate(path)}>
          <Statistic title={label} value={data?.counts[key] ?? '—'} />
        </button>)}
      </div>
    </Card>
    <Card title="工作进度" loading={!data && !error} extra={<Button type="link" onClick={() => setOpen(true)}>全部工作记录</Button>}>
      {data?.items.length ? data.items.slice(0, 3).map((item, index) => card(item, index === 0))
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工作记录" />}
    </Card>
    <Drawer open={open} title="全部工作记录" onClose={() => setOpen(false)} size="large">
      <Space wrap>
        <Input aria-label="搜索工作记录" placeholder="搜索名称、版本或操作" allowClear value={search} onChange={event => setSearch(event.target.value)} />
        <Select aria-label="筛选工作记录" value={filter} onChange={setFilter} options={[
          { value: '', label: '全部类型' }, { value: 'invalid', label: '已失效' },
          ...Object.entries(workLabels).map(([value, label]) => ({ value, label })),
        ]} />
      </Space>
      {error && <Alert type="error" showIcon title={error} />}
      {records.length ? records.map(item => card(item)) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配记录" />}
    </Drawer>
  </>
}
