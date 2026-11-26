import React, { useEffect, useState } from 'react'
import {
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
  CheckCircle2,
  Clock,
  AlertCircle,
  Loader2,
  Radio,
} from 'lucide-react'
import { api } from '../api/client'

interface Props {
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'run', label: '进行中' },
  { key: 'retry', label: '待重试' },
  { key: 'done', label: '已完成' },
  { key: 'fail', label: '已失败' },
]

const CAT_BADGE: Record<string, { label: string; cls: string }> = {
  run: { label: '进行中', cls: 'bg-sky-50 text-sky-700 border-sky-200' },
  retry: { label: '待重试', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  done: { label: '已完成', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  fail: { label: '已失败', cls: 'bg-rose-50 text-rose-700 border-rose-200' },
}

export default function TasksPage({ onToast }: Props) {
  const [filter, setFilter] = useState('all')
  const [queueData, setQueueData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [sseConnected, setSseConnected] = useState(false)

  // Detail modal state
  const [detailTarget, setDetailTarget] = useState<{ kind: string; id: string } | null>(null)
  const [detailData, setDetailData] = useState<any>(null)
  const [detailMode, setDetailMode] = useState<'all' | 'failed'>('all')
  const [detailPage, setDetailPage] = useState(1)
  const [detailLoading, setDetailLoading] = useState(false)
  const [retryingId, setRetryingId] = useState<string | null>(null)

  const fetchQueue = async () => {
    setLoading(true)
    try {
      const res = await api.listTasks('all', 120)
      setQueueData(res)
    } catch (err: any) {
      onToast(err?.message || '加载任务队列失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchQueue()
    const es = new EventSource('/api/v1/events/stream', { withCredentials: true })
    es.onopen = () => setSseConnected(true)
    es.onerror = () => setSseConnected(false)
    es.addEventListener('tasks', (ev: MessageEvent) => {
      try {
        setQueueData(JSON.parse(ev.data))
        setLoading(false)
        setSseConnected(true)
      } catch {}
    })
    return () => es.close()
  }, [])

  const loadDetail = async (kind: string, id: string, page = 1, mode: 'all' | 'failed' = 'all') => {
    setDetailLoading(true)
    try {
      const res =
        kind === 'live'
          ? await api.getLiveTask(id)
          : await api.getGroupDetail(id, page, mode)
      setDetailData(res)
    } catch (err: any) {
      onToast(err?.message || '获取任务明细失败', 'error')
    } finally {
      setDetailLoading(false)
    }
  }

  const openDetail = (task: any) => {
    setDetailTarget({ kind: task.kind, id: task.id })
    setDetailMode(task.category === 'retry' || task.category === 'fail' ? 'failed' : 'all')
    setDetailPage(1)
    loadDetail(task.kind, task.id, 1, task.category === 'retry' || task.category === 'fail' ? 'failed' : 'all')
  }

  const handleRetryGroup = async (groupId: string) => {
    setRetryingId(groupId)
    try {
      const res = await api.retryGroup(groupId)
      onToast(res.message || (res.ok ? '已触发立即重试' : '重试失败'), res.ok ? 'success' : 'error')
      fetchQueue()
      if (detailTarget?.id === groupId) {
        loadDetail('group', groupId, detailPage, detailMode)
      }
    } catch (err: any) {
      onToast(err?.message || '触发重试失败', 'error')
    } finally {
      setRetryingId(null)
    }
  }

  const handleDeleteGroup = async (groupId: string) => {
    if (!window.confirm('确认删除该任务记录？（不会删除网盘已下载文件）')) return
    try {
      await api.deleteGroup(groupId)
      onToast('已删除任务记录', 'success')
      if (detailTarget?.id === groupId) {
        setDetailTarget(null)
        setDetailData(null)
      }
      fetchQueue()
    } catch (err: any) {
      onToast(err?.message || '删除失败', 'error')
    }
  }

  const handleClearCategory = async (cat: 'done' | 'fail') => {
    const label = cat === 'done' ? '已完成' : '已失败'
    if (!window.confirm(`确认清空所有「${label}」任务记录？（不影响网盘文件）`)) return
    try {
      const res = await api.clearGroups(cat)
      onToast(`已清空 ${res.removed} 条${label}记录`, 'success')
      fetchQueue()
    } catch (err: any) {
      onToast(err?.message || '清空失败', 'error')
    }
  }

  const allTasks = queueData?.tasks || []
  const visibleTasks = filter === 'all' ? allTasks : allTasks.filter((t: any) => t.category === filter)
  const counts = queueData?.counts || { all: 0, run: 0, retry: 0, done: 0, fail: 0 }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg sm:text-xl font-bold text-slate-900 flex items-center gap-2">
            统一任务队列
            <span
              className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full ${
                sseConnected ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'
              }`}
            >
              <Radio className={`w-3 h-3 ${sseConnected ? 'animate-pulse' : ''}`} />
              {sseConnected ? '实时同步' : '重连中'}
            </span>
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            自动重试：{queueData?.auto_retry_enabled ? `已开启（上限 ${queueData.retry_max_wait_label}）` : '已关闭（可点击详情手动重试）'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {(filter === 'done' || filter === 'fail') && counts[filter] > 0 && (
            <button
              onClick={() => handleClearCategory(filter)}
              className="px-3 py-2 rounded-xl bg-rose-50 text-rose-700 hover:bg-rose-100 text-xs font-medium flex items-center gap-1"
            >
              <Trash2 className="w-3.5 h-3.5" />
              清空{filter === 'done' ? '已完成' : '已失败'}
            </button>
          )}
          <button
            onClick={fetchQueue}
            disabled={loading}
            className="px-3 py-2 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {/* 5 状态筛选栏（手机可横向滚动） */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 no-scrollbar">
        {FILTERS.map((f) => {
          const active = filter === f.key
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`shrink-0 px-3.5 py-2 rounded-xl text-xs font-medium border transition flex items-center gap-1.5 ${
                active
                  ? 'bg-indigo-600 text-white border-indigo-600 shadow-xs'
                  : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              }`}
            >
              <span>{f.label}</span>
              <span
                className={`px-1.5 py-0.2 rounded-full text-[11px] ${
                  active ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {counts[f.key] ?? 0}
              </span>
            </button>
          )
        })}
      </div>

      {/* 任务卡片列表 */}
      {visibleTasks.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-200/80 p-10 text-center text-sm text-slate-400">
          当前筛选下暂无任务记录
        </div>
      ) : (
        <div className="space-y-2.5">
          {visibleTasks.map((t: any) => {
            const badge = CAT_BADGE[t.category] || CAT_BADGE.done
            const pct = t.total > 0 ? Math.round((t.success / t.total) * 100) : 0
            return (
              <div
                key={`${t.kind}-${t.id}`}
                onClick={() => openDetail(t)}
                className="cursor-pointer bg-white rounded-2xl border border-slate-200/80 hover:border-indigo-300 p-4 shadow-xs transition active:scale-[0.995]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full border ${badge.cls}`}>
                        {badge.label}
                      </span>
                      <span className="text-sm font-semibold text-slate-900 truncate">{t.title}</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                      <span>成功 {t.success}/{t.total}</span>
                      <span>第 {t.attempt} 次尝试</span>
                      {t.stage && t.kind === 'live' && <span className="text-sky-600">{t.stage}</span>}
                      {t.next_retry_text && <span className="text-amber-600">下次重试：{t.next_retry_text}</span>}
                      <span>{t.created_text}</span>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-sm font-bold text-slate-800">{pct}%</div>
                  </div>
                </div>
                <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden mt-3">
                  <div
                    className={`h-full rounded-full transition-all ${
                      t.category === 'fail'
                        ? 'bg-rose-500'
                        : t.category === 'retry'
                        ? 'bg-amber-500'
                        : t.category === 'run'
                        ? 'bg-sky-500'
                        : 'bg-emerald-500'
                    }`}
                    style={{ width: `${Math.max(pct, t.category === 'run' ? 8 : 0)}%` }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 任务明细抽屉 / 弹窗 */}
      {detailTarget && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-end sm:items-center justify-center sm:p-4">
          <div className="bg-white w-full sm:max-w-2xl max-h-[88vh] rounded-t-3xl sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between gap-2">
              <div className="min-w-0">
                <h3 className="text-base font-bold text-slate-900 truncate">
                  {detailData?.display_name || detailData?.title || '任务明细'}
                </h3>
                {detailData?.save_path && (
                  <p className="text-[11px] text-slate-500 font-mono truncate mt-0.5">{detailData.save_path}</p>
                )}
              </div>
              <button
                onClick={() => {
                  setDetailTarget(null)
                  setDetailData(null)
                }}
                className="p-2 rounded-xl text-slate-400 hover:text-slate-700 hover:bg-slate-100"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-5 overflow-y-auto flex-1 space-y-4">
              {detailLoading && !detailData ? (
                <div className="py-12 flex justify-center">
                  <Loader2 className="w-7 h-7 animate-spin text-indigo-600" />
                </div>
              ) : detailData ? (
                <>
                  {detailData.kind === 'group' && (
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs bg-slate-50 rounded-xl p-3 border border-slate-100">
                      <div className="space-y-1">
                        <div>
                          成功 <b className="text-emerald-600">{detailData.summary.success}</b> · 待重试{' '}
                          <b className="text-amber-600">{detailData.summary.failed}</b> · 已失败{' '}
                          <b className="text-rose-600">{detailData.summary.dead}</b> · 共 <b>{detailData.summary.total}</b>
                        </div>
                        <div className="text-slate-500">
                          第 {detailData.attempt} 次尝试
                          {detailData.next_retry_text ? ` · ${detailData.next_retry_text}` : ''}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            const nextMode = detailMode === 'failed' ? 'all' : 'failed'
                            setDetailMode(nextMode)
                            setDetailPage(1)
                            loadDetail('group', detailTarget.id, 1, nextMode)
                          }}
                          className="px-2.5 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-700 hover:bg-slate-100"
                        >
                          {detailMode === 'failed' ? '显示全部' : '只看未完成'}
                        </button>
                        {(detailData.summary.failed > 0 || detailData.summary.dead > 0) && (
                          <button
                            onClick={() => handleRetryGroup(detailData.group_id)}
                            disabled={retryingId === detailData.group_id}
                            className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 flex items-center gap-1 disabled:opacity-60"
                          >
                            {retryingId === detailData.group_id ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <RotateCcw className="w-3.5 h-3.5" />
                            )}
                            立即重试
                          </button>
                        )}
                        <button
                          onClick={() => handleDeleteGroup(detailData.group_id)}
                          className="p-1.5 rounded-lg text-rose-600 hover:bg-rose-50"
                          title="删除记录"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  )}

                  {/* 逐条磁力明细 */}
                  <div className="divide-y divide-slate-100 border border-slate-100 rounded-xl">
                    {(detailData.items || []).map((it: any) => (
                      <div key={it.index} className="p-3 flex items-start justify-between gap-3 text-xs">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            {it.status === 'success' ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                            ) : it.status === 'failed' || it.status === 'dead' ? (
                              <AlertCircle className="w-4 h-4 text-rose-500 shrink-0" />
                            ) : (
                              <Clock className="w-4 h-4 text-sky-500 shrink-0" />
                            )}
                            <span className="font-medium text-slate-800 break-all">{it.name}</span>
                          </div>
                          {(it.magnet || it.link) && (
                            <div className="text-[11px] text-slate-400 font-mono truncate mt-0.5 pl-5.5">
                              {it.magnet || it.link}
                            </div>
                          )}
                        </div>
                        <div className="shrink-0 text-right text-[11px] text-slate-500">
                          {detailData.kind === 'live' ? (
                            <span>{it.percent ?? 0}%</span>
                          ) : (
                            <span>第 {it.attempt} 次</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  {detailData.kind === 'group' && detailData.total_pages > 1 && (
                    <div className="flex items-center justify-center gap-3 text-xs">
                      <button
                        disabled={detailPage <= 1}
                        onClick={() => {
                          const p = detailPage - 1
                          setDetailPage(p)
                          loadDetail('group', detailTarget.id, p, detailMode)
                        }}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 disabled:opacity-40"
                      >
                        上一页
                      </button>
                      <span className="text-slate-500">
                        {detailData.page} / {detailData.total_pages}
                      </span>
                      <button
                        disabled={detailPage >= detailData.total_pages}
                        onClick={() => {
                          const p = detailPage + 1
                          setDetailPage(p)
                          loadDetail('group', detailTarget.id, p, detailMode)
                        }}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 disabled:opacity-40"
                      >
                        下一页
                      </button>
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
