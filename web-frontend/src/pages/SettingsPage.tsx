import React, { useEffect, useRef, useState } from 'react'
import {
  Key,
  Copy,
  RefreshCw,
  Shield,
  Terminal,
  Download,
  RotateCcw,
  Eye,
  EyeOff,
  ExternalLink,
  Info,
} from 'lucide-react'
import { api } from '../api/client'

interface Props {
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}

export default function SettingsPage({ onToast }: Props) {
  const [settings, setSettings] = useState<any>(null)
  const [guideHtml, setGuideHtml] = useState<string | null>(null)
  const [showKey, setShowKey] = useState(false)

  // Password form
  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [newUser, setNewUser] = useState('')

  // Live logs
  const [logLines, setLogLines] = useState<string[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const logBoxRef = useRef<HTMLDivElement | null>(null)

  const loadSettings = async () => {
    try {
      const res = await api.getSettings()
      setSettings(res)
      setNewUser(res.username || 'admin')
    } catch (err: any) {
      onToast(err?.message || '读取设置失败', 'error')
    }
  }

  useEffect(() => {
    loadSettings()

    const es = new EventSource('/api/v1/logs/stream?lines=150', { withCredentials: true })
    es.addEventListener('log_init', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data)
        setLogLines(data.lines || [])
      } catch {}
    })
    es.addEventListener('log_append', (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data)
        if (Array.isArray(data.lines) && data.lines.length > 0) {
          setLogLines((prev) => [...prev, ...data.lines].slice(-500))
        }
      } catch {}
    })
    return () => es.close()
  }, [])

  useEffect(() => {
    if (autoScroll && logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
    }
  }, [logLines, autoScroll])

  const patch = async (body: any, msg?: string) => {
    try {
      const res = await api.updateSettings(body)
      setSettings(res)
      if (res.auto_retry_guide_html) {
        setGuideHtml(res.auto_retry_guide_html)
      } else if (body.auto_retry_enabled === false) {
        setGuideHtml(null)
      }
      if (msg) onToast(msg, 'success')
    } catch (err: any) {
      onToast(err?.message || '更新设置失败', 'error')
    }
  }

  const copyApiKey = async () => {
    if (!settings?.api_key) return
    try {
      await navigator.clipboard.writeText(settings.api_key)
      onToast('API Key 已复制到剪贴板', 'success')
    } catch {
      onToast('复制失败，请手动选中复制', 'error')
    }
  }

  const handleRotateKey = async () => {
    if (!window.confirm('重新生成 API Key 后，旧 Key 将立即失效。确认继续？')) return
    try {
      const res = await api.rotateApiKey()
      setSettings((s: any) => ({ ...s, api_key: res.api_key }))
      setShowKey(true)
      onToast('已生成新的 API Key', 'success')
    } catch (err: any) {
      onToast(err?.message || '重置 API Key 失败', 'error')
    }
  }

  const handleChangePwd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!curPwd || newPwd.length < 6) {
      onToast('请输入当前密码，且新密码不少于 6 位', 'error')
      return
    }
    try {
      await api.changePassword(curPwd, newPwd, newUser)
      setCurPwd('')
      setNewPwd('')
      onToast('登录账号/密码已更新', 'success')
    } catch (err: any) {
      onToast(err?.message || '修改密码失败', 'error')
    }
  }

  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://your-host:8115'

  return (
    <div className="space-y-4 sm:space-y-5">
      <div>
        <h2 className="text-lg sm:text-xl font-bold text-slate-900">系统设置与实时日志</h2>
        <p className="text-xs text-slate-500 mt-0.5">管理自动重试策略、默认目录、API Key、账号安全及运行日志</p>
      </div>

      {/* 自动重试机制设置 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">自动重试机制</h3>
            <p className="text-xs text-slate-500 mt-0.5">默认关闭；开启后对未完成任务按 2 倍退避自动重新提交</p>
          </div>
          <button
            type="button"
            onClick={() =>
              patch(
                { auto_retry_enabled: !settings?.auto_retry_enabled },
                settings?.auto_retry_enabled ? '已关闭自动重试机制' : '已开启自动重试机制',
              )
            }
            className={`relative w-12 h-7 rounded-full transition ${
              settings?.auto_retry_enabled ? 'bg-indigo-600' : 'bg-slate-300'
            }`}
          >
            <span
              className={`absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                settings?.auto_retry_enabled ? 'translate-x-5' : ''
              }`}
            />
          </button>
        </div>

        {guideHtml && (
          <div className="rounded-xl bg-indigo-50/80 border border-indigo-200 p-3.5 text-xs text-indigo-950 space-y-1">
            <div className="flex items-center gap-1.5 font-semibold text-indigo-700 mb-1">
              <Info className="w-4 h-4" /> 自动重试使用说明
            </div>
            <div
              className="leading-relaxed whitespace-pre-line"
              dangerouslySetInnerHTML={{ __html: guideHtml }}
            />
          </div>
        )}

        <div>
          <div className="text-xs text-slate-500 mb-2">最长等待上限（每次翻倍，达到上限后标记失败）</div>
          <div className="flex flex-wrap gap-2">
            {(settings?.retry_cap_options || []).map((opt: any) => {
              const active = settings?.retry_max_wait_hours === opt.hours
              return (
                <button
                  key={opt.hours}
                  type="button"
                  onClick={() => patch({ retry_max_wait_hours: opt.hours }, `重试上限已改为 ${opt.label}`)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-medium border transition ${
                    active
                      ? 'bg-indigo-600 text-white border-indigo-600'
                      : 'bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100'
                  }`}
                >
                  {opt.label}
                </button>
              )
            })}
          </div>
          {settings?.retry_wait_chain && (
            <div className="mt-2.5 text-[11px] text-slate-500 bg-slate-50 rounded-xl px-3 py-2 font-mono break-all">
              等待序列（共 {settings.retry_attempt_count} 次）：{settings.retry_wait_chain}
            </div>
          )}
        </div>
      </div>

      {/* 联动与快捷维护 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Web / API 下载完成同步通知 Telegram</h3>
            <p className="text-xs text-slate-500 mt-0.5">开启后，从 Web 或 REST API 提交的下载完成时会同步发一条汇总到 TG</p>
          </div>
          <button
            type="button"
            onClick={() =>
              patch(
                { notify_tg_on_web_download: !settings?.notify_tg_on_web_download },
                '已更新 Telegram 通知设置',
              )
            }
            className={`relative w-12 h-7 rounded-full transition ${
              settings?.notify_tg_on_web_download ? 'bg-indigo-600' : 'bg-slate-300'
            }`}
          >
            <span
              className={`absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                settings?.notify_tg_on_web_download ? 'translate-x-5' : ''
              }`}
            />
          </button>
        </div>

        <div className="pt-2 border-t border-slate-100 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="text-slate-600 truncate">
            当前默认目录：<code className="text-indigo-700 font-medium">{settings?.default_save_path || '未设置'}</code>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={async () => {
                await api.resetDefaultPath()
                loadSettings()
                onToast('已重置记住的默认目录', 'success')
              }}
              className="px-3 py-1.5 rounded-xl border border-slate-200 hover:bg-slate-50 text-slate-700 inline-flex items-center gap-1"
            >
              <RotateCcw className="w-3.5 h-3.5" /> 重置默认目录
            </button>
            <button
              type="button"
              onClick={async () => {
                await api.reloadConfig()
                loadSettings()
                onToast('已热重载 config.yaml', 'success')
              }}
              className="px-3 py-1.5 rounded-xl bg-slate-900 text-white hover:bg-slate-800 inline-flex items-center gap-1"
            >
              <RefreshCw className="w-3.5 h-3.5" /> 重载配置
            </button>
          </div>
        </div>
      </div>

      {/* API Key 与外部调用示例 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
            <Key className="w-4 h-4 text-indigo-500" />
            REST API 密钥与调用
          </h3>
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-indigo-600 hover:text-indigo-700 inline-flex items-center gap-1"
          >
            打开 Swagger /docs <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1 min-w-[200px] px-3 py-2 rounded-xl bg-slate-50 border border-slate-200 font-mono text-xs text-slate-800 truncate">
            {showKey ? settings?.api_key : '••••••••••••••••••••••••••••••••'}
          </div>
          <button
            type="button"
            onClick={() => setShowKey((v) => !v)}
            className="p-2 rounded-xl border border-slate-200 hover:bg-slate-50 text-slate-600"
            title={showKey ? '隐藏' : '显示'}
          >
            {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
          <button
            type="button"
            onClick={copyApiKey}
            className="px-3 py-2 rounded-xl bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-xs font-medium inline-flex items-center gap-1"
          >
            <Copy className="w-3.5 h-3.5" /> 复制
          </button>
          <button
            type="button"
            onClick={handleRotateKey}
            className="px-3 py-2 rounded-xl border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-medium"
          >
            重新生成
          </button>
        </div>

        <pre className="text-[11px] bg-slate-900 text-slate-200 rounded-xl p-3 overflow-x-auto leading-relaxed">
{`curl -X POST "${origin}/api/v1/downloads/submit" \\
  -H "X-API-Key: ${showKey ? settings?.api_key || 'YOUR_API_KEY' : '<YOUR_API_KEY>'}" \\
  -H "Content-Type: application/json" \\
  -d '{"links":["magnet:?xt=urn:btih:..."],"notify_tg":true}'`}
        </pre>
      </div>

      {/* 修改 Web 登录密码 */}
      <form onSubmit={handleChangePwd} className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-3">
        <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
          <Shield className="w-4 h-4 text-indigo-500" />
          修改 Web 登录账号 / 密码
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <input
            type="text"
            value={newUser}
            onChange={(e) => setNewUser(e.target.value)}
            placeholder="用户名"
            className="px-3 py-2 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white text-xs sm:text-sm"
          />
          <input
            type="password"
            value={curPwd}
            onChange={(e) => setCurPwd(e.target.value)}
            placeholder="当前密码"
            className="px-3 py-2 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white text-xs sm:text-sm"
          />
          <input
            type="password"
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
            placeholder="新密码（至少 6 位）"
            className="px-3 py-2 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white text-xs sm:text-sm"
          />
        </div>
        <button
          type="submit"
          className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium"
        >
          保存新密码
        </button>
      </form>

      {/* 实时日志终端 */}
      <div className="bg-slate-900 rounded-2xl border border-slate-800 p-4 shadow-xs space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
            <Terminal className="w-4 h-4 text-emerald-400" />
            实时运行日志（115bot.log）
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-slate-400 inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="accent-emerald-500"
              />
              自动滚动
            </label>
            <a
              href="/api/v1/logs/download"
              className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] inline-flex items-center gap-1"
            >
              <Download className="w-3 h-3" /> 下载完整日志
            </a>
          </div>
        </div>
        <div
          ref={logBoxRef}
          className="h-64 overflow-y-auto font-mono text-[11px] leading-relaxed text-slate-300 bg-slate-950/70 rounded-xl p-3 space-y-0.5"
        >
          {logLines.length === 0 ? (
            <div className="text-slate-500">暂无日志输出...</div>
          ) : (
            logLines.map((line, idx) => (
              <div
                key={idx}
                className={`break-all ${
                  line.includes('ERROR')
                    ? 'text-rose-400'
                    : line.includes('WARN')
                    ? 'text-amber-300'
                    : 'text-slate-300'
                }`}
              >
                {line}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
