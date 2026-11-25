import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Space, Switch, Table } from 'antd'
import { accountRequest, roleLabels, type Account, type Credentials, type Role } from '../api/webSession'

const roles = Object.entries(roleLabels).map(([value, label]) => ({ value, label }))

export default function AccountManagement({ onClose }: { onClose: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm<Credentials & { role: Role }>()
  useEffect(() => {
    let active = true
    accountRequest<Account[]>('accounts').then(
      (rows) => { if (active) setAccounts(rows) },
      (reason: Error) => { if (active) setError(reason.message) },
    )
    return () => { active = false }
  }, [])

  async function save(values: Account | (Credentials & { role: Role })) {
    setError('')
    setBusy(true)
    try {
      if ('enabled' in values) {
        const updated = await accountRequest<Account>(`accounts/${encodeURIComponent(values.username)}`, 'PATCH', {
          role: values.role, enabled: values.enabled,
        })
        setAccounts((rows) => rows.map((row) => row.username === updated.username ? updated : row))
      } else {
        const created = await accountRequest<Account>('accounts', 'POST', values)
        setAccounts((rows) => [...rows, created])
        form.resetFields()
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return <Modal open title="用户与权限管理" onCancel={onClose} footer={null} width={680}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message={error} />}
      <p>管理员可管理账号和角色。评测操作员可使用数据管理与数据评测功能。</p>
      <Table<Account> size="small" rowKey="username" dataSource={accounts} columns={[
        { title: '用户名', dataIndex: 'username' },
        { title: '角色', render: (_, row) => <Select aria-label={`${row.username} 的角色`} value={row.role} options={roles} disabled={busy || row.username === 'admin'} onChange={(role: Role) => void save({ ...row, role })} /> },
        { title: '启用', render: (_, row) => <Switch aria-label={`启用 ${row.username}`} checked={Boolean(row.enabled)} disabled={busy || row.username === 'admin'} onChange={(enabled) => void save({ ...row, enabled })} /> },
      ]} />
      <Form form={form} layout="vertical" initialValues={{ role: 'operator' }} onFinish={save}>
        <Form.Item name="username" label="新账号" rules={[{ required: true, pattern: /^[a-zA-Z0-9_.-]{1,48}$/, message: '请输入 1–48 个字母、数字或 . _ -' }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6, max: 128, message: '请输入 6–128 位密码' }]}><Input.Password autoComplete="new-password" /></Form.Item>
        <Form.Item name="role" label="角色" rules={[{ required: true }]}><Select options={roles} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>创建账号</Button>
      </Form>
    </Space>
  </Modal>
}
