/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-22 02:38:27
 * @LastEditTime: 2026-10-22 02:59:55
 * @Description: 
 */

import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import MysqlDatasourcePage from './pages/MysqlDatasourcePage'

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <MysqlDatasourcePage />
    </ConfigProvider>
  )
}
