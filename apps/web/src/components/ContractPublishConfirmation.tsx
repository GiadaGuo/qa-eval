import { Button, Checkbox } from 'antd'
import type { ReactNode } from 'react'

export default function ContractPublishConfirmation({ confirmed, onConfirm, disabled, loading, onPublish, children, findings }: {
  confirmed: boolean
  onConfirm: (value: boolean) => void
  disabled: boolean
  loading: boolean
  onPublish: () => void
  children: ReactNode
  findings?: ReactNode
}) {
  return <>
    <Checkbox checked={confirmed} onChange={event => onConfirm(event.target.checked)}>{children}</Checkbox>
    {findings}
    <Button type="primary" disabled={!confirmed || disabled} loading={loading} onClick={onPublish}>确认并发布多指标质量契约</Button>
  </>
}
