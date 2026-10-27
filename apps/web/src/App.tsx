/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * MVP 入口：闭环 1 本地验证报告 + 闭环 2 数据集上传与 Profile。
 */

import { useState } from 'react'
import { ConfigProvider, Layout, Menu } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import DatasetDetailPage from './pages/DatasetDetailPage'
import DatasetListPage from './pages/DatasetListPage'
import DatasetVersionPage from './pages/DatasetVersionPage'
import LocalValidationReportPage from './pages/LocalValidationReportPage'
import MetricRegistryPage from './pages/MetricRegistryPage'
import type { ContractSeed } from './pages/MetricRegistryPage'
import QualityContractBuilderPage from './pages/QualityContractBuilderPage'
import EvaluationResultsPage from './pages/EvaluationResultsPage'
import MysqlDatasourcePage from './pages/MysqlDatasourcePage'
import type { Dataset, DatasetVersion } from './api/datasetVersions'

const { Header, Content } = Layout

type ViewKey = 'local-validation' | 'datasets' | 'metrics' | 'results'

export default function App() {
  const [currentView, setCurrentView] = useState<ViewKey>('datasets')
  const [selectedDataset, setSelectedDataset] = useState<Dataset | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<DatasetVersion | null>(null)
  const [mappingVersion, setMappingVersion] = useState<DatasetVersion | null>(null)
  const [contractSeed, setContractSeed] = useState<ContractSeed | null>(null)

  const handleSelectDataset = (dataset: Dataset) => {
    setSelectedDataset(dataset)
    setSelectedVersion(null)
  }

  const handleBackToDatasets = () => {
    setSelectedDataset(null)
    setSelectedVersion(null)
  }

  const handleSelectVersion = (version: DatasetVersion) => {
    setSelectedVersion(version)
  }

  const handleBackToDatasetDetail = () => {
    setSelectedVersion(null)
  }

  const renderContent = () => {
    if (currentView === 'local-validation') {
      return <LocalValidationReportPage />
    }
    if (currentView === 'results') return <EvaluationResultsPage />
    if (currentView === 'metrics') {
      if (contractSeed) return <QualityContractBuilderPage seed={contractSeed} onBack={() => setContractSeed(null)} />
      return <MetricRegistryPage onBuildContract={setContractSeed} />
    }
    if (mappingVersion) return <MysqlDatasourcePage versionId={mappingVersion.id} onBack={() => setMappingVersion(null)} />

    if (selectedVersion && selectedDataset) {
      return (
        <DatasetVersionPage
          key={selectedVersion.id}
          dataset={selectedDataset}
          version={selectedVersion}
          onBack={handleBackToDatasetDetail}
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
        />
      )
    }

    return <DatasetListPage onSelectDataset={handleSelectDataset} />
  }

  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center' }}>
          <div style={{ color: 'white', fontSize: 18, fontWeight: 'bold', marginRight: 48 }}>
            QA Eval
          </div>
          <Menu
            theme="dark"
            mode="horizontal"
            selectedKeys={[currentView]}
            onClick={(e) => {
              setCurrentView(e.key as ViewKey)
              setSelectedDataset(null)
              setSelectedVersion(null)
              setMappingVersion(null)
              setContractSeed(null)
            }}
            items={[
              { key: 'datasets', label: '数据集管理' },
              { key: 'metrics', label: '指标管理' },
              { key: 'results', label: '评测结果' },
              { key: 'local-validation', label: '本地验证报告' },
            ]}
          />
        </Header>
        <Content style={{ padding: 24 }}>{renderContent()}</Content>
      </Layout>
    </ConfigProvider>
  )
}
