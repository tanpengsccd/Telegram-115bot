import React, { useEffect, useState } from 'react'
import {
  LayoutDashboard,
  DownloadCloud,
  ListTodo,
  Cloud,
  Settings,
  LogOut,
  CloudDownload,
  Loader2,
  ExternalLink,
} from 'lucide-react'
import { api, setUnauthorizedHandler } from './api/client'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import DownloadPage from './pages/DownloadPage'
import TasksPage from './pages/TasksPage'
import CloudPage from './pages/CloudPage'
import SettingsPage from './pages/SettingsPage'

const NAV_ITEMS = [
  { id: 'dashboard', label: '总览', icon: LayoutDashboard },
  { id: 'download', label: '下载', icon: DownloadCloud },
  { id: 'tasks', label: '队列', icon: ListTodo },
  { id: 'cloud', label: '云端', icon: Cloud },
  { id: 'settings', label: '设置', icon: Settings },
]

export default function App() {
  const [checkingAuth, setCheckingAuth] = useState(true)
  const [username, setUsername] = useState<string | null>(null)
  const [tab, setTab] = useState('dashboard')
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null)

  const showToast = (msg: string, type: 'success' | 'error' | 'info' = 'info') => {
    setToast({ msg, type })
  }

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUsername(null)
    })
    api
      .me()
      .then((res) => setUsername(res.username))
      .catch(() => setUsername(null))
      .finally(() => setCheckingAuth(false))
  }, [])

  const handleLogout = async () => {
    try {
      await api.logout()
    } catch {}
    setUsername(null)
  }

  if (checkingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
      </div>
    )
  }

  if (!username) {
    return <LoginPage onSuccess={(u) => setUsername(u)} />
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col md:flex-row">
      {/* 桌面端左侧固定导航栏 */}
      <aside className="hidden md:flex md:flex-col md:w-60 md:fixed md:inset-y-0 bg-white border-r border-slate-200/80 z-30">
        <div className="px-5 py-5 flex items-center gap-3 border-b border-slate-100">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-600 to-sky-500 flex items-center justify-center text-white shadow-xs">
            <CloudDownload className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-bold text-slate-900 truncate">Telegram-115Bot</div>
            <div className="text-[11px] text-slate-400 truncate">Web & REST API</div>
          </div>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const active = tab === item.id
            return (
              <button
                key={item.id}
                onClick={() => setTab(item.id)}
                className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition ${
                  active
                    ? 'bg-indigo-50 text-indigo-700'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`}
              >
                <Icon className="w-4 h-4" />
                {item.label}
              </button>
            )
          })}
        </nav>

        <div className="p-3 border-t border-slate-100 space-y-1.5">
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="w-full flex items-center justify-between px-3.5 py-2 rounded-xl text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            <span>Swagger API 文档</span>
            <ExternalLink className="w-3.5 h-3.5 text-slate-400" />
          </a>
          <div className="flex items-center justify-between px-3.5 py-2">
            <span className="text-xs text-slate-500 truncate">账号：{username}</span>
            <button
              onClick={handleLogout}
              className="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition"
              title="退出登录"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* 手机端顶部精简导航栏 */}
      <header className="md:hidden sticky top-0 z-30 bg-white/90 backdrop-blur-md border-b border-slate-200/80 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-600 to-sky-500 flex items-center justify-center text-white">
            <CloudDownload className="w-4 h-4" />
          </div>
          <span className="text-sm font-bold text-slate-900">115Bot 控制台</span>
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-slate-500 hover:text-rose-600 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-100"
        >
          <LogOut className="w-3.5 h-3.5" /> 退出
        </button>
      </header>

      {/* 主内容区域 */}
      <main className="flex-1 md:pl-60">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-4 sm:py-6 safe-content-pb md:pb-8">
          {tab === 'dashboard' && <DashboardPage onNavigate={setTab} onToast={showToast} />}
          {tab === 'download' && <DownloadPage onNavigate={setTab} onToast={showToast} />}
          {tab === 'tasks' && <TasksPage onToast={showToast} />}
          {tab === 'cloud' && <CloudPage onToast={showToast} />}
          {tab === 'settings' && <SettingsPage onToast={showToast} />}
        </div>
      </main>

      {/* 手机端底部固定 5-Tab 导航栏（适配 Safe Area） */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur-md border-t border-slate-200/90 safe-pb">
        <div className="grid grid-cols-5">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const active = tab === item.id
            return (
              <button
                key={item.id}
                onClick={() => setTab(item.id)}
                className={`flex flex-col items-center justify-center py-2.5 gap-1 text-[11px] font-medium transition ${
                  active ? 'text-indigo-600' : 'text-slate-500'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span>{item.label}</span>
              </button>
            )
          })}
        </div>
      </nav>

      {/* 全局 Toast 提示 */}
      {toast && (
        <div className="fixed top-4 right-4 left-4 sm:left-auto sm:max-w-sm z-50 pointer-events-none flex justify-center sm:justify-end">
          <div
            className={`px-4 py-2.5 rounded-xl shadow-lg text-xs sm:text-sm font-medium border ${
              toast.type === 'success'
                ? 'bg-emerald-600 text-white border-emerald-500'
                : toast.type === 'error'
                ? 'bg-rose-600 text-white border-rose-500'
                : 'bg-slate-900 text-white border-slate-800'
            }`}
          >
            {toast.msg}
          </div>
        </div>
      )}
    </div>
  )
}
