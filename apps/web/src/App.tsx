/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 应用路由入口：/ 为 Welcome 首页，/app 进入原有业务功能页面。
 */

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Dashboard from './Dashboard'
import HomePage from './pages/HomePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/app/*" element={<Dashboard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
