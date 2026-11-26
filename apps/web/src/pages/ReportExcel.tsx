import { useEffect, useState } from 'react'
import { Button, Space } from 'antd'

export default function ReportExcel({ runId, reportId }: { runId: string; reportId?: string }) {
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const url = `/api/v1/evaluation-runs/${runId}/report-export`
  const query = reportId ? `?report_id=${encodeURIComponent(reportId)}` : ''
  const requestUrl = url + query

  useEffect(() => {
    if (status !== 'RUNNING') return
    const controller = new AbortController()
    let timer: number
    const poll = async () => {
      try {
        const response = await fetch(requestUrl, { signal: controller.signal })
        if (!response.ok) throw new Error(`读取导出状态失败（HTTP ${response.status}）`)
        const result = await response.json()
        if (controller.signal.aborted) return
        if (result.status === 'RUNNING') timer = window.setTimeout(poll, 1000)
        else {
          setStatus(result.status)
          if (result.status !== 'READY') setError(result.detail || '导出未完成，请重试。')
        }
      } catch (reason) {
        if (!controller.signal.aborted) { setStatus('FAILED'); setError(String(reason)) }
      }
    }
    timer = window.setTimeout(poll, 1000)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [status, requestUrl])

  const start = async () => {
    setError('')
    setStatus('STARTING')
    try {
      const response = await fetch(requestUrl, { method: 'POST' })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `导出失败（HTTP ${response.status}）`)
      setStatus(result.status)
    } catch (reason) { setStatus('FAILED'); setError(String(reason)) }
  }

  return <Space wrap>
    {status === 'READY' ? <Button href={`${requestUrl}${query ? '&' : '?'}download=true`}>下载 Excel</Button>
      : <Button loading={['STARTING', 'RUNNING'].includes(status)} onClick={() => void start()}>导出 Excel</Button>}
    {error && <span role="alert">{error}</span>}
  </Space>
}
