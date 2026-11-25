import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Alert, Button, ConfigProvider, Form, Input, Checkbox, Spin } from 'antd'
import { ExperimentOutlined, LockOutlined, UserOutlined } from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'
import { accountRequest, type Account, type Credentials } from './api/webSession'
import Dashboard from './Dashboard'
import './workspace.css'

const rememberedUsername = 'qa-eval.remembered-username'

function Login({ onLogin }: { onLogin: (account: Account) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const username = localStorage.getItem(rememberedUsername)

  async function submit({ remember, ...credentials }: Credentials & { remember: boolean }) {
    setBusy(true)
    setError('')
    try {
      const account = await accountRequest<Account>('session', 'POST', credentials)
      if (remember) localStorage.setItem(rememberedUsername, account.username)
      else localStorage.removeItem(rememberedUsername)
      onLogin(account)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return <main className="qa-login">
    <section className="qa-login-brand">
      <div className="qa-brand"><ExperimentOutlined /><span>QA Eval<small>数据质量评测平台</small></span></div>
      <div className="qa-login-intro">
        <h1>在同一工作台<br />管理数据与质量评测</h1>
        <p>管理数据集及版本、配置质量指标，并跟踪评测任务和结果。</p>
      </div>
    </section>
    <section className="qa-login-form">
      <div>
        <h2>登录 QA Eval</h2>
        <Form layout="vertical" size="large" onFinish={submit} initialValues={{ username: username ?? 'admin', remember: Boolean(username) }}>
          {error && <Alert type="error" showIcon message={error} />}
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="输入用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="输入密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="remember" valuePropName="checked"><Checkbox>记住账号</Checkbox></Form.Item>
          <Button block type="primary" htmlType="submit" loading={busy}>登录</Button>
        </Form>
      </div>
    </section>
  </main>
}

export default function App() {
  const [account, setAccount] = useState<Account | null>()
  useEffect(() => {
    let active = true
    accountRequest<Account>('session').then(
      (user) => { if (active) setAccount(user) },
      () => { if (active) setAccount(null) },
    )
    return () => { active = false }
  }, [])

  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#14668f', borderRadius: 10 } }}>
    <BrowserRouter>
      {account === undefined ? <div className="qa-loading"><Spin size="large" /></div> : <Routes>
        <Route path="/" element={account ? <Navigate to="/app" replace /> : <Login onLogin={setAccount} />} />
        <Route path="/app/*" element={account ? <Dashboard account={account} onLogout={async () => {
          await accountRequest('session', 'DELETE')
          setAccount(null)
        }} /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>}
    </BrowserRouter>
  </ConfigProvider>
}
