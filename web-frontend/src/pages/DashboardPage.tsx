import React, { useEffect, useRef, useState } from 'react'
import {
  CheckCircle2,
  AlertTriangle,
  HardDrive,
  Ticket,
  FolderOpen,
  QrCode,
  RefreshCw,
  ArrowRight,
  X,
  Loader2,
  ExternalLink,
} from 'lucide-react'
import { api } from '../api/client'

interface Props {
  onNavigate: (tab: string) => void
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}

export default function DashboardPage({ onNavigate, onToast }: Props) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [qrOpen, setQrOpen] = useState(false)
  const [qrSession, setQrSession] = useState<any>(null)
  const [qrLoading, setQrLoading] = useState(false)
  const pollRef = useRef<number | null>(null)

  const loadStatus = async () => {
    setLoading(true)
    try {
      const res = await api.status()
      setData(res)
    } catch (err: any) {
      onToast(err?.message || '获取状态失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStatus()
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  }, [])

  const stopQrPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startQrAuth = async () => {
    stopQrPolling()
    setQrOpen(true)
    setQrLoading(true)
    setQrSession(null)
    try {
      const res = await api.qrStart()
      setQrSession(res)
      pollRef.current = window.setInterval(async () => {
        try {
          const st = await api.qrStatus(res.session_id)
          setQrSession(st)
          if (st.status === 'authorized') {
            stopQrPolling()
            onToast('115 扫码授权成功！', 'success')
            setTimeout(() => {
              setQrOpen(false)
              loadStatus()
            }, 900)
          } else if (st.status === 'expired' || st.status === 'error') {
            stopQrPolling()
          }
        } catch {
          stopQrPolling()
        }
      }, 2000)
    } catch (err: any) {
      onToast(err?.message || '生成二维码失败', 'error')
      setQrOpen(false)
    } finally {
      setQrLoading(false)
    }
  }

  const closeQr = () => {
    stopQrPolling()
    setQrOpen(false)
  }

  const storagePct =
    data?.storage?.total_bytes > 0
      ? Math.min(100, Math.round((data.storage.used_bytes / data.storage.total_bytes) * 100))
      : 0

  const quotaPct =
    data?.quota?.total > 0
      ? Math.min(100, Math.round((data.quota.surplus / data.quota.total) * 100))
      : 0

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-slate-900">账号与运行总览</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {data?.version || 'v3.2.13'} · 查询时间 {data?.server_time || '--'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={startQrAuth}
            className="min-h-[38px] px-3.5 py-2 rounded-xl bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-xs sm:text-sm font-medium flex items-center gap-1.5 transition"
          >
            <QrCode className="w-4 h-4" />
            115 扫码授权
          </button>
          <button
            onClick={loadStatus}
            disabled={loading}
            className="min-h-[38px] px-3 py-2 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs sm:text-sm font-medium flex items-center gap-1.5 transition"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {!data && loading ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-36 rounded-2xl bg-white border border-slate-200/80 animate-pulse" />
          ))}
        </div>
      ) : (
        <>
          {/* 顶部 3 张核心状态卡片 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* 115 账号卡 */}
            <div className="bg-white rounded-2xl border border-slate-200/80 p-5 shadow-xs">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-medium text-slate-500">115 开放平台账号</span>
                {data?.authorized ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    已授权
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 px-2.5 py-0.5 rounded-full">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    未授权
                  </span>
                )}
              </div>
              {data?.authorized && data.account ? (
                <div>
                  <div className="text-lg font-bold text-slate-900 truncate">{data.account.user_name}</div>
                  <div className="text-xs text-slate-500 mt-1">ID: {data.account.user_id}</div>
                  <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between text-xs">
                    <span className="text-indigo-600 font-medium">{data.account.vip_level}</span>
                    <span className="text-slate-500">到期：{data.account.vip_expire}</span>
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-slate-600">Token 尚未配置或已失效，请点击扫码重新授权。</p>
                  <button
                    onClick={startQrAuth}
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-700 inline-flex items-center gap-1"
                  >
                    立即扫码授权 <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>

            {/* 离线配额卡 */}
            <div className="bg-white rounded-2xl border border-slate-200/80 p-5 shadow-xs">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-medium text-slate-500">离线下载配额</span>
                <Ticket className="w-4 h-4 text-indigo-500" />
              </div>
              {data?.quota ? (
                <div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-2xl font-bold text-slate-900">{data.quota.surplus}</span>
                    <span className="text-xs text-slate-500">/ {data.quota.total}（已用 {data.quota.used}）</span>
                  </div>
                  <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden mt-3">
                    <div
                      className="h-full bg-gradient-to-r from-indigo-500 to-sky-500 rounded-full transition-all"
                      style={{ width: `${quotaPct}%` }}
                    />
                  </div>
                  {data.quota.reset_packages?.length > 0 && (
                    <div className="mt-2.5 space-y-1">
                      {data.quota.reset_packages.slice(0, 2).map((pkg: any, idx: number) => (
                        <div key={idx} className="text-[11px] text-slate-500 truncate">
                          {pkg.name}：{pkg.expire_text}（{pkg.countdown}）
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-sm text-slate-400">暂无配额信息</div>
              )}
            </div>

            {/* 网盘容量卡 */}
            <div className="bg-white rounded-2xl border border-slate-200/80 p-5 shadow-xs">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-medium text-slate-500">115 网盘容量</span>
                <HardDrive className="w-4 h-4 text-sky-500" />
              </div>
              {data?.storage ? (
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-slate-900">{data.storage.remain}</span>
                    <span className="text-xs text-slate-500">剩余</span>
                  </div>
                  <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden mt-3">
                    <div
                      className="h-full bg-gradient-to-r from-sky-500 to-cyan-400 rounded-full transition-all"
                      style={{ width: `${storagePct}%` }}
                    />
                  </div>
                  <div className="mt-2.5 flex items-center justify-between text-[11px] text-slate-500">
                    <span>已用 {data.storage.used}</span>
                    <span>总计 {data.storage.total}</span>
                  </div>
                </div>
              ) : (
                <div className="text-sm text-slate-400">暂无容量信息</div>
              )}
            </div>
          </div>

          {/* 快捷入口 + 下载目录 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-white rounded-2xl border border-slate-200/80 p-5 shadow-xs space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                  <FolderOpen className="w-4 h-4 text-indigo-500" />
                  已配置下载目录
                </h3>
                <button
                  onClick={() => onNavigate('download')}
                  className="text-xs font-medium text-indigo-600 hover:text-indigo-700 inline-flex items-center gap-1"
                >
                  去提交下载 <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="text-xs bg-indigo-50/70 border border-indigo-100 rounded-xl px-3.5 py-2.5 flex items-center justify-between gap-2">
                <span className="text-slate-600 shrink-0">当前默认目录</span>
                <code className="text-indigo-700 font-medium truncate">
                  {data?.default_save_path || '未设置'}
                </code>
              </div>
              <div className="divide-y divide-slate-100 max-h-56 overflow-y-auto">
                {(data?.categories || []).flatMap((cat: any) =>
                  (cat.path_map || []).map((pm: any) => (
                    <div key={`${cat.name}-${pm.path}`} className="py-2 flex items-center justify-between gap-3 text-xs">
                      <span className="text-slate-700 font-medium shrink-0">
                        {cat.display_name}{pm.name && pm.name !== cat.display_name ? ` / ${pm.name}` : ''}
                      </span>
                      <code className="text-slate-500 truncate">{pm.path}</code>
                    </div>
                  )),
                )}
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200/80 p-5 shadow-xs space-y-3">
              <h3 className="text-sm font-semibold text-slate-900">快捷状态与外部 API</h3>
              <div className="grid grid-cols-2 gap-2.5 text-xs">
                <div className="rounded-xl bg-slate-50 border border-slate-100 p-3">
                  <div className="text-slate-500">自动重试机制</div>
                  <div className={`mt-1 font-semibold ${data?.auto_retry_enabled ? 'text-emerald-600' : 'text-slate-700'}`}>
                    {data?.auto_retry_enabled ? `已开启（上限 ${data.retry_max_wait_label}）` : '已关闭（默认）'}
                  </div>
                </div>
                <div className="rounded-xl bg-slate-50 border border-slate-100 p-3">
                  <div className="text-slate-500">Web 完成通知 TG</div>
                  <div className="mt-1 font-semibold text-slate-700">
                    {data?.notify_tg_on_web_download ? '已开启' : '已关闭'}
                  </div>
                </div>
              </div>
              <div className="pt-1 flex flex-wrap gap-2">
                <button
                  onClick={() => onNavigate('tasks')}
                  className="px-3.5 py-2 rounded-xl bg-slate-900 text-white text-xs font-medium hover:bg-slate-800 transition"
                >
                  查看实时任务队列
                </button>
                <button
                  onClick={() => onNavigate('cloud')}
                  className="px-3.5 py-2 rounded-xl bg-slate-100 text-slate-700 text-xs font-medium hover:bg-slate-200 transition"
                >
                  管理 115 云端任务
                </button>
                <a
                  href="/docs"
                  target="_blank"
                  rel="noreferrer"
                  className="px-3.5 py-2 rounded-xl border border-slate-200 text-slate-700 text-xs font-medium hover:bg-slate-50 inline-flex items-center gap-1 transition"
                >
                  Swagger API 文档 <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            </div>
          </div>
        </>
      )}

      {/* 115 扫码授权弹窗 */}
      {qrOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6 relative">
            <button
              onClick={closeQr}
              className="absolute right-4 top-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
            >
              <X className="w-5 h-5" />
            </button>
            <h3 className="text-base font-bold text-slate-900 mb-1">115 开放平台扫码授权</h3>
            <p className="text-xs text-slate-500 mb-4">打开手机 115 App 扫一扫确认登录</p>

            <div className="flex flex-col items-center justify-center py-2">
              {qrLoading || !qrSession?.qr_data_url ? (
                <div className="w-52 h-52 rounded-xl bg-slate-100 flex items-center justify-center">
                  <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
                </div>
              ) : (
                <img
                  src={qrSession.qr_data_url}
                  alt="115 QR Code"
                  className="w-56 h-56 rounded-xl border border-slate-200 p-2 bg-white"
                />
              )}
              <div
                className={`mt-4 text-xs font-medium px-3 py-1.5 rounded-full ${
                  qrSession?.status === 'authorized'
                    ? 'bg-emerald-50 text-emerald-700'
                    : qrSession?.status === 'scanned'
                    ? 'bg-sky-50 text-sky-700'
                    : qrSession?.status === 'expired' || qrSession?.status === 'error'
                    ? 'bg-rose-50 text-rose-700'
                    : 'bg-slate-100 text-slate-600'
                }`}
              >
                {qrSession?.message || '正在生成二维码...'}
              </div>
              {(qrSession?.status === 'expired' || qrSession?.status === 'error') && (
                <button
                  onClick={startQrAuth}
                  className="mt-3 px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700"
                >
                  重新生成二维码
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
