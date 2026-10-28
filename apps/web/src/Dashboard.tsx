/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 原有业务功能页面外壳：数据集管理、指标管理、评测结果。
 */

import { useState } from 'react'
import { ConfigProvider, Layout, Menu, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import DatasetDetailPage from './pages/DatasetDetailPage'
import DatasetListPage from './pages/DatasetListPage'
import DatasetVersionPage from './pages/DatasetVersionPage'
import DocumentQualityReportPage from './pages/DocumentQualityReportPage'
import MetricRegistryPage from './pages/MetricRegistryPage'
import type { ContractSeed } from './pages/MetricRegistryPage'
import QualityContractBuilderPage from './pages/QualityContractBuilderPage'
import EvaluationResultsPage from './pages/EvaluationResultsPage'
import MysqlDatasourcePage from './pages/MysqlDatasourcePage'
import AgentEvaluationWorkspace from './pages/AgentEvaluationWorkspace'
import type { Dataset, DatasetVersion } from './api/datasetVersions'

const { Header, Content } = Layout

type ViewKey = 'datasets' | 'metrics' | 'results'

type ReportView = { reportId: string }

export default function Dashboard() {
  const [currentView, setCurrentView] = useState<ViewKey>('datasets')
  const [selectedDataset, setSelectedDataset] = useState<Dataset | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<DatasetVersion | null>(null)
  const [mappingVersion, setMappingVersion] = useState<DatasetVersion | null>(null)
  const [contractSeed, setContractSeed] = useState<ContractSeed | null>(null)
  const [reportView, setReportView] = useState<ReportView | null>(null)
  const [agentVersion, setAgentVersion] = useState<DatasetVersion | null>(null)

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
  }

  const renderContent = () => {
    if (currentView === 'results') return <EvaluationResultsPage />
    if (currentView === 'metrics') {
      if (contractSeed) return <QualityContractBuilderPage seed={contractSeed} onBack={() => setContractSeed(null)} />
      return <MetricRegistryPage onBuildContract={setContractSeed} />
    }
    if (mappingVersion) return <MysqlDatasourcePage versionId={mappingVersion.id} onBack={() => setMappingVersion(null)} />

    if (reportView) {
      return <DocumentQualityReportPage reportId={reportView.reportId} onBack={() => setReportView(null)} />
    }

    if (agentVersion && selectedDataset) {
      return <AgentEvaluationWorkspace dataset={selectedDataset} version={agentVersion} onBack={() => setAgentVersion(null)} />
    }

    if (selectedVersion && selectedDataset) {
      return (
        <DatasetVersionPage
          key={selectedVersion.id}
          dataset={selectedDataset}
          version={selectedVersion}
          onBack={handleBackToDatasetDetail}
          onOpenAgentWorkspace={() => setAgentVersion(selectedVersion)}
        />
      )
    }

    if (selectedDataset) {
      return (
        <DatasetDetailPage
          key={selectedDataset.id}
          dataset={selectedDataset}
          onBack={handleBackToDatasets}
          onSelectVersion={handleSelectVersion}
          onConfigureMapping={setMappingVersion}
          onViewReport={(reportId) => setReportView({ reportId })}
        />
      )
    }

    return <DatasetListPage onSelectDataset={handleSelectDataset} />
  }

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#6F9E88',
          colorInfo: '#527F9D',
          colorLink: '#527F9D',
          colorText: '#263B36',
          colorTextSecondary: '#71837E',
          colorBgLayout: '#F7FAF7',
          colorBgContainer: '#FFFFFF',
          colorBorder: '#E4EBE7',
          colorBorderSecondary: '#EDF2EF',
          borderRadius: 8,
          borderRadiusLG: 12,
          controlHeight: 36,
          boxShadowSecondary: '0 8px 24px rgba(61, 91, 80, 0.08)',
        },
        components: {
          Button: { primaryShadow: '0 2px 8px rgba(111, 158, 136, 0.22)' },
          Card: { headerBg: '#FFFFFF' },
          Menu: {
            itemBg: 'transparent',
            itemColor: '#526A63',
            itemHoverColor: '#527F9D',
            itemSelectedColor: '#416F5B',
            horizontalItemSelectedColor: '#416F5B',
            horizontalItemHoverColor: '#527F9D',
          },
          Table: {
            headerBg: '#EEF5F1',
            headerColor: '#334E46',
            rowHoverBg: '#F3F8F5',
            borderColor: '#E4EBE7',
            cellPaddingBlock: 14,
          },
          Tabs: { itemSelectedColor: '#416F5B', inkBarColor: '#6F9E88' },
        },
      }}
    >
      <Layout className="app-shell">
        <Header className="app-header">
          <div className="app-brand">QA Eval</div>
          <nav className="app-nav" aria-label="主导航">
            <Menu
              mode="horizontal"
              disabledOverflow
              selectedKeys={[currentView]}
              onClick={(e) => {
                setCurrentView(e.key as ViewKey)
                setSelectedDataset(null)
                setSelectedVersion(null)
                setMappingVersion(null)
                setContractSeed(null)
                setReportView(null)
                setAgentVersion(null)
              }}
              items={[
                { key: 'datasets', label: '数据集管理' },
                { key: 'metrics', label: '指标管理' },
                { key: 'results', label: '评测结果' },
              ]}
            />
          </nav>
        </Header>
        <Content className="app-content">{renderContent()}</Content>
      </Layout>
    </ConfigProvider>
  )
}
