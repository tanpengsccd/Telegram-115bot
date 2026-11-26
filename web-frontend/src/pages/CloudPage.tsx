import React, { useEffect, useState } from 'react'
import { Cloud, RefreshCw, Trash2, CheckCircle2, AlertCircle, Clock, ChevronLeft, ChevronRight } from 'lucide-react'
import { api } from '../api/client'

interface Props {
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}

export default function CloudPage({ onToast }: Props) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busyHash, setBusyHash] = useState<string | null>(null)

  const load = async (targetPage = page) => {
    setLoading(true)
    try {
      const res = await api.cloudTasks(targetPage)
      setData(res)
      setPage(res.page || targetPage)
    } catch (err: any) {
      onToast(err?.message || '获取 115 云端任务失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(1)
  }, [])

  const handleDelete = async (infoHash: string, name: string, deleteSource: boolean) => {
    const tip = deleteSource
      ? `确定删除云端任务「${name}」并同时删除网盘源文件吗？`
      : `确定仅移除云端任务记录「${name}」？（保留已下载文件）`
    if (!window.confirm(tip)) return
    setBusyHash(infoHash)
    try {
      const res = await api.deleteCloudTask(infoHash, deleteSource)
      onToast(res.ok ? '已删除云端任务' : '删除失败', res.ok ? 'success' : 'error')
      load(page)
    } catch (err: any) {
      onToast(err?.message || '删除失败', 'error')
    } finally {
      setBusyHash(null)
    }
  }

  const handleClear = async (flag: 1 | 2 | 4) => {
    const labels: Record<number, string> = { 4: '已完成任务记录', 2: '失败任务', 1: '全部云端任务记录（不删源文件）' }
    if (!window.confirm(`确认清空 115 云端「${labels[flag]}」？`)) return
    try {
      const res = await api.clearCloudTasks(flag)
      onToast(res.ok ? `已清空 ${labels[flag]}` : '清空失败', res.ok ? 'success' : 'error')
      load(1)
    } catch (err: any) {
      onToast(err?.message || '清空失败', 'error')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-slate-900 flex items-center gap-2">
            <Cloud className="w-5 h-5 text-sky-500" />
            115 官方云端离线任务
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            总计 {data?.total_count ?? 0} 条 · 第 {data?.page ?? 1} / {data?.page_count ?? 1} 页
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => handleClear(4)}
            className="px-3 py-1.5 rounded-xl bg-emerald-50 text-emerald-700 hover:bg-emerald-100 text-xs font-medium"
          >
            清空已完成
          </button>
          <button
            onClick={() => handleClear(2)}
            className="px-3 py-1.5 rounded-xl bg-amber-50 text-amber-700 hover:bg-amber-100 text-xs font-medium"
          >
            清空失败
          </button>
          <button
            onClick={() => handleClear(1)}
            className="px-3 py-1.5 rounded-xl bg-rose-50 text-rose-700 hover:bg-rose-100 text-xs font-medium"
          >
            清空全部记录
          </button>
          <button
            onClick={() => load(page)}
            disabled={loading}
            className="px-3 py-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium flex items-center gap-1"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {data && !data.ok && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-2xl p-4 text-xs">
          {data.error || '无法连接 115 云端任务接口，请检查 115 授权状态。'}
        </div>
      )}

      <div className="bg-white rounded-2xl border border-slate-200/80 shadow-xs overflow-hidden divide-y divide-slate-100">
        {loading && !data ? (
          <div className="p-10 text-center text-sm text-slate-400">正在加载 115 云端任务...</div>
        ) : (data?.tasks || []).length === 0 ? (
          <div className="p-10 text-center text-sm text-slate-400">当前页没有任何云端离线任务</div>
        ) : (
          (data?.tasks || []).map((t: any) => (
            <div key={t.info_hash || t.name} className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  {t.status === 2 || t.percent === 100 ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                  ) : t.status === -1 ? (
                    <AlertCircle className="w-4 h-4 text-rose-500 shrink-0" />
                  ) : (
                    <Clock className="w-4 h-4 text-sky-500 shrink-0" />
                  )}
                  <span className="text-sm font-medium text-slate-900 break-all">{t.name}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 pl-6">
                  <span className="font-medium text-slate-700">{t.status_label}</span>
                  {t.info_hash && <span className="font-mono text-[11px]">{t.info_hash.slice(0, 14)}...</span>}
                  {t.add_time_text && <span>{t.add_time_text}</span>}
                </div>
              </div>

              {t.info_hash && (
                <div className="flex items-center gap-2 pl-6 sm:pl-0 shrink-0">
                  <button
                    disabled={busyHash === t.info_hash}
                    onClick={() => handleDelete(t.info_hash, t.name, false)}
                    className="px-2.5 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 text-xs"
                  >
                    仅删记录
                  </button>
                  <button
                    disabled={busyHash === t.info_hash}
                    onClick={() => handleDelete(t.info_hash, t.name, true)}
                    className="px-2.5 py-1.5 rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100 text-xs inline-flex items-center gap-1"
                  >
                    <Trash2 className="w-3 h-3" />
                    含源文件
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {(data?.page_count || 1) > 1 && (
        <div className="flex items-center justify-center gap-3 text-xs">
          <button
            disabled={page <= 1 || loading}
            onClick={() => load(page - 1)}
            className="px-3.5 py-2 rounded-xl bg-white border border-slate-200 disabled:opacity-40 flex items-center gap-1"
          >
            <ChevronLeft className="w-4 h-4" /> 上一页
          </button>
          <span className="text-slate-600 font-medium">
            第 {page} / {data.page_count} 页
          </span>
          <button
            disabled={page >= data.page_count || loading}
            onClick={() => load(page + 1)}
            className="px-3.5 py-2 rounded-xl bg-white border border-slate-200 disabled:opacity-40 flex items-center gap-1"
          >
            下一页 <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  )
}
