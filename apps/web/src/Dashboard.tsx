import { useEffect, useState } from 'react'
import { Alert, App as AntApp, Avatar, Button, Card, ConfigProvider, Dropdown, Layout, Menu, theme } from 'antd'
import { ApartmentOutlined, BarChartOutlined, DatabaseOutlined, DownOutlined, ExperimentOutlined, HomeOutlined, LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, RightOutlined } from '@ant-design/icons'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { roleLabels, type Account } from './api/webSession'
import AccountManagement from './pages/AccountManagement'
import EvaluationWorkbench from './pages/EvaluationWorkbench'
import zhCN from 'antd/locale/zh_CN'

import DatasetDetailPage from './pages/DatasetDetailPage'
import DatasetIngestionPage from './pages/DatasetIngestionPage'
import DatasetListPage from './pages/DatasetListPage'
import DatasetVersionPage from './pages/DatasetVersionPage'
import DocumentQualityReportPage from './pages/DocumentQualityReportPage'
import DatasetMappingEntry from './pages/DatasetMappingEntry'
import { getDataset, getDatasetVersion, type Dataset, type DatasetVersion } from './api/datasetVersions'

const { Header, Content, Sider } = Layout

type ViewKey = 'home' | 'datasets' | 'evaluation'
const sections = {
  home: { title: '个人工作台', path: '/app', icon: <HomeOutlined /> },
  datasets: { title: '数据管理中心', path: '/app/datasets', icon: <DatabaseOutlined /> },
  evaluation: { title: '数据评测中心', path: '/app/evaluation', icon: <BarChartOutlined /> },
}

type ReportView = { reportId: string }

export default function Dashboard({ account, onLogout }: { account: Account; onLogout: () => Promise<void> }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [query, setQuery] = useSearchParams()
  const rawVersionId = pathname === sections.datasets.path ? query.get('rawVersion') : null
  const [restoreError, setRestoreError] = useState('')
  const ingestionPath = sections.datasets.path + '/ingest'
  const currentView = pathname === ingestionPath ? 'datasets' : (Object.keys(sections) as ViewKey[]).find((key) => sections[key].path === pathname) ?? 'home'
  const [collapsed, setCollapsed] = useState(false)
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [logoutError, setLogoutError] = useState('')
  const [selectedDataset, setSelectedDataset] = useState<Dataset | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<DatasetVersion | null>(null)
  const [mappingVersion, setMappingVersion] = useState<DatasetVersion | null>(null)
  const [reportView, setReportView] = useState<ReportView | null>(null)
  const [agentVersion, setAgentVersion] = useState<DatasetVersion | null>(null)

  useEffect(() => {
    if (!rawVersionId || selectedVersion?.id === rawVersionId) return
    let active = true
    getDatasetVersion(rawVersionId).then(async (version) => {
      const dataset = await getDataset(version.dataset_id)
      if (active) {
        setSelectedDataset(dataset)
        setSelectedVersion(version)
        setRestoreError('')
      }
    }).catch((failure: Error) => {
      if (active) setRestoreError(failure.message)
    })
    return () => { active = false }
  }, [rawVersionId, selectedVersion?.id])

  const closeRawVersion = () => {
    const updated = new URLSearchParams(query)
    updated.delete('rawVersion')
    setQuery(updated, { replace: true })
    setRestoreError('')
  }

  const handleSelectDataset = (dataset: Dataset) => {
    setSelectedDataset(dataset)
    setSelectedVersion(null)
    setAgentVersion(null)
  }

  const handleBackToDatasets = () => {
    setSelectedDataset(null)
    setSelectedVersion(null)
    setAgentVersion(null)
  }

  const handleSelectVersion = (version: DatasetVersion) => {
    setSelectedVersion(version)
    setAgentVersion(null)
  }

  const handleBackToDatasetDetail = () => {
    setSelectedVersion(null)
    setReportView(null)
    setAgentVersion(null)
    if (rawVersionId) closeRawVersion()
  }

  const openSection = (view: ViewKey) => {
    setMappingVersion(null)
    setReportView(null)
    handleBackToDatasets()
    navigate(sections[view].path)
  }

  const renderContent = () => {
    if (currentView === 'home') return <div className="qa-home">
      <section className="qa-home-welcome"><h1>{account.username}，你好</h1><p>从数据集管理或质量评测开始工作。</p></section>
      <Card title="快速开始">
        <div className="qa-home-grid">
          {(['datasets', 'evaluation'] as const).map((key) => <button key={key} className="qa-shortcut" onClick={() => openSection(key)}>
            <span className="qa-shortcut-icon">{sections[key].icon}</span><RightOutlined className="qa-shortcut-arrow" />
            <strong>{sections[key].title}</strong>
            <span>{key === 'datasets' ? '查看数据集、版本与数据说明' : '配置指标、运行评测与查看结果'}</span>
          </button>)}
        </div>
      </Card>
    </div>
    if (currentView === 'evaluation') return <EvaluationWorkbench initialDataset={selectedDataset} initialVersion={agentVersion}
      onOpenDatasets={(dataset) => {
        openSection('datasets')
        if (dataset) handleSelectDataset(dataset)
      }}
      onBackToVersion={() => { setAgentVersion(null); navigate(sections.datasets.path) }} />
    if (pathname === ingestionPath) return <DatasetIngestionPage
      onBack={() => navigate(sections.datasets.path)}
      onComplete={(dataset) => { handleSelectDataset(dataset); navigate(sections.datasets.path) }}
    />
    if (mappingVersion && selectedDataset) return <DatasetMappingEntry
      key={mappingVersion.id} version={mappingVersion} dataset={selectedDataset}
      onPublished={(version) => {
        setMappingVersion(null)
        handleSelectVersion(version)
      }}
      onBack={() => setMappingVersion(null)} />

    if (reportView) {
      return <DocumentQualityReportPage reportId={reportView.reportId} onBack={() => setReportView(null)} />
    }

    if (rawVersionId && selectedVersion?.id !== rawVersionId) {
      return restoreError
        ? <Alert type="error" title={restoreError} action={<Button onClick={closeRawVersion}>返回数据集</Button>} />
        : <Card loading />
    }

    if (selectedVersion && selectedDataset) {
      return (
        <DatasetVersionPage
          key={selectedVersion.id}
          dataset={selectedDataset}
          version={selectedVersion}
          onBack={handleBackToDatasetDetail}
          onOpenAgentWorkspace={() => {
            setAgentVersion(selectedVersion)
            navigate(sections.evaluation.path)
          }}
        />
      )
    }

    if (selectedDataset) {
      return (
        <DatasetDetailPage
          key={selectedDataset.id}
          dataset={selectedDataset}
          onBack={handleBackToDatasets}
          onEvaluate={() => navigate(sections.evaluation.path)}
          onSelectVersion={handleSelectVersion}
          onConfigureMapping={(version) => version.record_schema === '3.0' ? handleSelectVersion(version) : setMappingVersion(version)}
          onViewReport={(reportId) => setReportView({ reportId })}
        />
      )
    }

    return <DatasetListPage onSelectDataset={handleSelectDataset} onIngest={() => navigate(ingestionPath)} />
  }

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#14668f',
          colorInfo: '#346a90',
          colorLink: '#346a90',
          colorText: '#233c50',
          colorTextSecondary: '#526d82',
          colorBgLayout: '#f5f9fd',
          colorBgContainer: '#FFFFFF',
          colorBorder: '#dce8f1',
          colorBorderSecondary: '#eaf1f7',
          borderRadius: 8,
          borderRadiusLG: 12,
          controlHeight: 36,
          boxShadowSecondary: '0 8px 24px rgba(36, 76, 110, 0.08)',
        },
        components: {
          Button: { primaryShadow: '0 2px 8px rgba(56, 124, 164, 0.22)' },
          Card: { headerBg: '#FFFFFF' },
          Menu: {
            itemBg: 'transparent',
            itemColor: '#49677d',
            itemHoverColor: '#346a90',
            itemSelectedColor: '#246188',
            horizontalItemSelectedColor: '#246188',
            horizontalItemHoverColor: '#346a90',
          },
          Table: {
            headerBg: '#eaf3fa',
            headerColor: '#304b60',
            rowHoverBg: '#f1f7fc',
            borderColor: '#dce8f1',
            cellPaddingBlock: 14,
          },
          Tabs: { itemSelectedColor: '#246188', inkBarColor: '#387ca4' },
        },
      }}
    >
      <AntApp>
        <Layout className="qa-shell">
          <Sider className="qa-sidebar" width={218} collapsedWidth={58} collapsed={collapsed} breakpoint="md" onBreakpoint={setCollapsed}>
            <div className="qa-brand"><ExperimentOutlined />{!collapsed && <span>QA Eval<small>数据质量评测平台</small></span>}</div>
            <Menu theme="dark" mode="inline" aria-label="主导航" inlineCollapsed={collapsed} selectedKeys={[currentView]}
              items={Object.entries(sections).map(([key, section]) => ({ key, label: section.title, icon: section.icon }))}
              onClick={({ key }) => openSection(key as ViewKey)} />
          </Sider>
          <Layout className="qa-main">
            <Header className="qa-header">
              <div className="qa-header-title">
                <Button type="text" aria-label={collapsed ? '展开菜单' : '收起菜单'} icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} />
                <strong>{sections[currentView].title}</strong>
                {currentView === 'evaluation' && <span className="qa-header-description">
                  选择数据集版本，配置质量契约，并查看评测任务和结果。
                </span>}
              </div>
              <Dropdown trigger={['click']} menu={{
                items: [
                  ...(account.role === 'admin' ? [{ key: 'accounts', label: '用户与权限管理', icon: <ApartmentOutlined /> }] : []),
                  { key: 'logout', label: '退出登录', icon: <LogoutOutlined /> },
                ],
                onClick: ({ key }) => {
                  if (key === 'accounts') setAccountsOpen(true)
                  else void onLogout().catch((error: Error) => setLogoutError(error.message))
                },
              }}>
                <button className="qa-account"><Avatar>{account.username.slice(0, 1).toUpperCase()}</Avatar><span>{account.username}<small>{roleLabels[account.role]}</small></span><DownOutlined /></button>
              </Dropdown>
            </Header>
            <Content className="qa-content">
              {logoutError && <p role="alert">{logoutError}</p>}
              {renderContent()}
            </Content>
          </Layout>
          {accountsOpen && <AccountManagement onClose={() => setAccountsOpen(false)} />}
        </Layout>
      </AntApp>
    </ConfigProvider>
  )
}
