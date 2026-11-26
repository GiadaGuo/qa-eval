import { useEffect, useState } from 'react'
import { Alert, Button, Space } from 'antd'

type ExportStatus = 'NOT_REQUESTED' | 'RUNNING' | 'FAILED' | 'READY'

export default function DecisionExport({ evidenceId, reason, reasonName }: {
  evidenceId: string
  reason?: string
  reasonName?: string
}) {
  const [jobs, setJobs] = useState<Record<string, ExportStatus>>({})
  const [error, setError] = useState('')
  const root = `/api/v1/evidence/${evidenceId}/decision-export`
  const endpoint = (code: string) => code ? `${root}?reason=${encodeURIComponent(code)}` : root
  const running = Object.entries(jobs).filter(([, status]) => status === 'RUNNING').map(([code]) => code)
  const pollKey = JSON.stringify(running)

  useEffect(() => {
    const codes: string[] = JSON.parse(pollKey)
    if (!codes.length) return
    const controller = new AbortController()
    const timer = window.setInterval(async () => {
      for (const code of codes) {
        try {
          const url = code ? `${root}?reason=${encodeURIComponent(code)}` : root
          const response = await fetch(url, { signal: controller.signal })
          if (!response.ok) throw new Error(`导出状态读取失败（HTTP ${response.status}）`)
          const { status } = await response.json() as { status: ExportStatus }
          if (!controller.signal.aborted) {
            setJobs(previous => ({ ...previous, [code]: status }))
            if (status === 'FAILED' || status === 'NOT_REQUESTED') setError('导出未完成，请重新发起。')
          }
        } catch (failure) {
          if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure))
        }
      }
    }, 1500)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [root, pollKey])

  const start = async (code: string) => {
    setError('')
    try {
      const response = await fetch(endpoint(code), { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `导出失败（HTTP ${response.status}）`)
      setJobs(previous => ({ ...previous, [code]: data.status }))
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    }
  }

  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Space wrap>
      <Button disabled={jobs[''] === 'RUNNING'} onClick={() => void start('')}>导出Excel · 全部原因</Button>
      {reason && <Button disabled={jobs[reason] === 'RUNNING'} onClick={() => void start(reason)}>导出Excel · 当前原因</Button>}
      {running.length > 0 && <span>正在后台导出</span>}
    </Space>
    {Object.entries(jobs).filter(([, status]) => status === 'READY').map(([code]) =>
      <Button key={code} type="link" href={`${endpoint(code)}${code ? '&' : '?'}download=true`}>
        下载 Excel · {code ? (code === reason ? reasonName : code) : '全部原因'}
      </Button>)}
    {error && <Alert type="warning" title={error} />}
  </Space>
}
