import React, { useEffect, useRef, useState } from 'react'
import {
  Upload,
  Link2,
  CheckSquare,
  Square,
  Folder,
  Send,
  Loader2,
  FileText,
  Trash2,
  Bell,
} from 'lucide-react'
import { api } from '../api/client'

interface ParsedLink {
  index: number
  kind: string
  link: string
  display_name: string
}

interface Props {
  onNavigate: (tab: string) => void
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}

const KIND_BADGE: Record<string, string> = {
  magnet: 'bg-indigo-50 text-indigo-700',
  ed2k: 'bg-amber-50 text-amber-700',
  thunder: 'bg-sky-50 text-sky-700',
  torrent: 'bg-emerald-50 text-emerald-700',
}

export default function DownloadPage({ onNavigate, onToast }: Props) {
  const [rawText, setRawText] = useState('')
  const [parsedLinks, setParsedLinks] = useState<ParsedLink[]>([])
  const [selectedIndexes, setSelectedIndexes] = useState<Set<number>>(new Set())
  const [sourceLabel, setSourceLabel] = useState('')
  const [folderName, setFolderName] = useState('')
  const [savePath, setSavePath] = useState('')
  const [categories, setCategories] = useState<any[]>([])
  const [notifyTg, setNotifyTg] = useState(true)
  const [parsing, setParsing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    api.status().then((st) => {
      setSavePath(st.default_save_path || '')
      setCategories(st.categories || [])
      setNotifyTg(Boolean(st.notify_tg_on_web_download))
    }).catch(() => {})
  }, [])

  const applyParsedResult = (links: ParsedLink[], suggestedFolder: string, label: string) => {
    setParsedLinks(links)
    setSelectedIndexes(new Set(links.map((l) => l.index)))
    setSourceLabel(label)
    if (suggestedFolder) setFolderName(suggestedFolder)
  }

  const handleFileSelected = async (file: File) => {
    setParsing(true)
    try {
      const res = await api.parseFile(file)
      if (!res.links || res.links.length === 0) {
        onToast(`${res.doc_kind || '文件'} 中未检测到有效下载链接`, 'error')
        return
      }
      applyParsedResult(res.links, res.suggested_folder_name || '', `${res.doc_kind} · ${res.file_name}`)
      onToast(`已识别 ${res.link_count} 个有效链接，勾选确认后即可下载`, 'success')
    } catch (err: any) {
      onToast(err?.message || '文件解析失败', 'error')
    } finally {
      setParsing(false)
    }
  }

  const handleParseText = async () => {
    if (!rawText.trim()) {
      onToast('请先粘贴包含链接的文本', 'error')
      return
    }
    setParsing(true)
    try {
      const res = await api.parseText(rawText)
      if (!res.links || res.links.length === 0) {
        onToast('未从文本中识别到磁力/ed2k/迅雷/种子链接', 'error')
        return
      }
      applyParsedResult(res.links, res.suggested_folder_name || '', '文本粘贴')
      onToast(`已识别 ${res.link_count} 个链接`, 'success')
    } catch (err: any) {
      onToast(err?.message || '文本解析失败', 'error')
    } finally {
      setParsing(false)
    }
  }

  const toggleIndex = (idx: number) => {
    const next = new Set(selectedIndexes)
    if (next.has(idx)) next.delete(idx)
    else next.add(idx)
    setSelectedIndexes(next)
  }

  const toggleAll = () => {
    if (selectedIndexes.size === parsedLinks.length) {
      setSelectedIndexes(new Set())
    } else {
      setSelectedIndexes(new Set(parsedLinks.map((l) => l.index)))
    }
  }

  const clearParsed = () => {
    setParsedLinks([])
    setSelectedIndexes(new Set())
    setSourceLabel('')
    setFolderName('')
  }

  const handleSubmit = async () => {
    const chosenLinks =
      parsedLinks.length > 0
        ? parsedLinks.filter((l) => selectedIndexes.has(l.index)).map((l) => l.link)
        : undefined

    if (parsedLinks.length > 0 && (!chosenLinks || chosenLinks.length === 0)) {
      onToast('请至少勾选 1 个链接', 'error')
      return
    }
    if (parsedLinks.length === 0 && !rawText.trim()) {
      onToast('请粘贴链接或上传链接文件', 'error')
      return
    }

    setSubmitting(true)
    try {
      const res = await api.submitDownload({
        links: chosenLinks,
        raw_text: parsedLinks.length === 0 ? rawText : undefined,
        save_path: savePath.trim() || undefined,
        custom_folder_name: folderName.trim() || undefined,
        notify_tg: notifyTg,
        remember_path: true,
      })
      onToast(`已添加 ${res.accepted_count} 个下载任务至 ${res.target_display_path}`, 'success')
      setRawText('')
      clearParsed()
      onNavigate('tasks')
    } catch (err: any) {
      onToast(err?.message || '提交下载失败', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const flatFolders = categories.flatMap((cat) =>
    (cat.path_map || []).map((pm: any) => ({
      label: cat.display_name + (pm.name && pm.name !== cat.display_name ? ` / ${pm.name}` : ''),
      path: pm.path,
    })),
  )

  return (
    <div className="space-y-4 sm:space-y-5">
      <div>
        <h2 className="text-lg sm:text-xl font-bold text-slate-900">新建离线下载</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          支持直接粘贴磁力 / ed2k / 迅雷 / 种子链接，或上传 .txt / .csv / .html / .htm / Excel / .torrent 文件
        </p>
      </div>

      {/* 文件拖拽上传区 */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          const f = e.dataTransfer.files?.[0]
          if (f) handleFileSelected(f)
        }}
        onClick={() => fileInputRef.current?.click()}
        className={`cursor-pointer rounded-2xl border-2 border-dashed p-5 sm:p-6 text-center transition ${
          dragOver
            ? 'border-indigo-500 bg-indigo-50/70'
            : 'border-slate-300 bg-white hover:border-indigo-400 hover:bg-slate-50/60'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.csv,.html,.htm,.xlsx,.xlsm,.xls,.torrent"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) handleFileSelected(f)
            e.target.value = ''
          }}
        />
        <div className="flex flex-col items-center gap-2">
          {parsing ? (
            <Loader2 className="w-7 h-7 text-indigo-600 animate-spin" />
          ) : (
            <div className="w-11 h-11 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center">
              <Upload className="w-5 h-5" />
            </div>
          )}
          <div className="text-sm font-medium text-slate-800">
            {parsing ? '正在解析文件链接...' : '点击选择或拖拽链接文件到此处'}
          </div>
          <div className="text-[11px] text-slate-500">
            支持 TXT · CSV · HTML/HTM · Excel (.xlsx/.xlsm/.xls) · .torrent（自动解析预览勾选）
          </div>
        </div>
      </div>

      {/* 粘贴链接区 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
            <Link2 className="w-4 h-4 text-indigo-500" />
            直接粘贴链接 / 批量文本
          </label>
          <button
            type="button"
            onClick={handleParseText}
            disabled={parsing || !rawText.trim()}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-700 disabled:opacity-40"
          >
            先识别预览
          </button>
        </div>
        <textarea
          rows={4}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder={'粘贴 magnet:? / ed2k:// / thunder:// / .torrent 链接（首尾非链接行自动作为文件夹名）'}
          className="w-full rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500 p-3 text-xs sm:text-sm font-mono"
        />
      </div>

      {/* 解析预览勾选列表 */}
      {parsedLinks.length > 0 && (
        <div className="bg-white rounded-2xl border border-indigo-200 p-4 sm:p-5 shadow-xs space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <FileText className="w-4 h-4 text-indigo-600" />
              <span>已解析 {parsedLinks.length} 个链接</span>
              <span className="text-xs font-normal text-slate-500">({sourceLabel})</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={toggleAll}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
              >
                {selectedIndexes.size === parsedLinks.length ? '取消全选' : '全选'} ({selectedIndexes.size}/{parsedLinks.length})
              </button>
              <button
                type="button"
                onClick={clearParsed}
                className="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50"
                title="清空预览"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="max-h-64 overflow-y-auto divide-y divide-slate-100 border border-slate-100 rounded-xl">
            {parsedLinks.map((item) => {
              const checked = selectedIndexes.has(item.index)
              return (
                <div
                  key={item.index}
                  onClick={() => toggleIndex(item.index)}
                  className={`flex items-start gap-2.5 p-2.5 cursor-pointer hover:bg-slate-50 transition ${
                    checked ? 'bg-indigo-50/30' : 'opacity-60'
                  }`}
                >
                  {checked ? (
                    <CheckSquare className="w-4 h-4 text-indigo-600 shrink-0 mt-0.5" />
                  ) : (
                    <Square className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${KIND_BADGE[item.kind] || 'bg-slate-100 text-slate-600'}`}>
                        {item.kind}
                      </span>
                      <span className="text-xs font-medium text-slate-800 truncate">{item.display_name}</span>
                    </div>
                    <div className="text-[11px] text-slate-400 font-mono truncate mt-0.5">{item.link}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 保存路径与选项 */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 sm:p-5 shadow-xs space-y-4">
        <div className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">
          <Folder className="w-4 h-4 text-indigo-500" />
          保存目录与提交选项
        </div>

        {flatFolders.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {flatFolders.map((f) => {
              const active = savePath === f.path
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setSavePath(f.path)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-medium border transition ${
                    active
                      ? 'bg-indigo-600 text-white border-indigo-600 shadow-xs'
                      : 'bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100'
                  }`}
                >
                  {f.label}
                </button>
              )
            })}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">115 保存目录（默认已选中记忆目录）</label>
            <input
              type="text"
              value={savePath}
              onChange={(e) => setSavePath(e.target.value)}
              placeholder="/影视/电影/外语电影"
              className="w-full px-3 py-2.5 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/30 text-xs sm:text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">子文件夹名（多链接批量下载时创建，可选）</label>
            <input
              type="text"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              placeholder="留空则自动生成或使用标题行"
              className="w-full px-3 py-2.5 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/30 text-xs sm:text-sm"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <label className="inline-flex items-center gap-2 cursor-pointer select-none text-xs text-slate-700">
            <input
              type="checkbox"
              checked={notifyTg}
              onChange={(e) => setNotifyTg(e.target.checked)}
              className="w-4 h-4 rounded accent-indigo-600"
            />
            <Bell className="w-3.5 h-3.5 text-slate-500" />
            完成后同步推送汇总到 Telegram
          </label>

          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="w-full sm:w-auto min-h-[44px] px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 active:scale-[0.99] text-white font-medium text-sm flex items-center justify-center gap-2 shadow-sm transition disabled:opacity-60"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {parsedLinks.length > 0
              ? `开始下载已选 (${selectedIndexes.size})`
              : '提交离线下载'}
          </button>
        </div>
      </div>
    </div>
  )
}
