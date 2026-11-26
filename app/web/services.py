# -*- coding: utf-8 -*-
"""Shared business service layer backing both the Web UI and external REST API."""

import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import init
from app.core.batch_task_manager import (
    delete_batch_task_group,
    get_batch_task_group_info,
    get_batch_task_items,
    get_group_actual_path,
    get_group_display_name,
    summarize_group_items,
)
from app.core.batch_task_retry import retry_batch_group_now
from app.handlers.download_handler import (
    DownloadUrlType,
    clear_user_last_save_path,
    download_executor,
    download_tasks_batch,
    extract_folder_name_from_text,
    get_default_save_path,
    get_file_confirm_auto_timeout_sec,
    get_user_last_save_path,
    remember_user_last_save_path,
    sanitize_folder_name,
)
from app.handlers.menu_handler import (
    BOT_VERSION,
    _fetch_cloud_tasks_page,
    _load_queue_groups,
    _norm_queue_filter,
    clear_queue_groups,
    get_panel_refresh_debounce_sec,
)
from app.utils.bot_settings import (
    RETRY_CAP_OPTIONS_HOURS,
    build_auto_retry_guide_text,
    build_retry_wait_chain_str,
    format_retry_cap_label,
    format_wait_duration,
    get_retry_max_wait_hours,
    is_auto_retry_enabled,
    set_auto_retry_enabled,
    set_retry_max_wait_hours,
)
from app.utils.file_link_parser import (
    build_preview_txt_filename,
    classify_link,
    extract_links_from_text_chunk,
    get_document_base_and_ext,
    parse_document_links,
)
from app.utils.task_tracker import (
    extract_display_name_from_link,
    format_retry_task_name,
    get_task,
    list_queue_tasks,
)
from app.web.auth import get_api_key, get_web_username
from app.web.config import get_notify_tg_on_web_download, set_notify_tg_on_web_download

TZ_CN = timezone(timedelta(hours=8))

_PARSER_KIND_TO_URL_TYPE = {
    "magnet": DownloadUrlType.MAGNET,
    "torrent": DownloadUrlType.TORRENT,
    "ed2k": DownloadUrlType.ED2K,
    "thunder": DownloadUrlType.THUNDER,
}


def get_effective_user_id() -> int:
    """Return primary TG user_id if configured, or 0 in pure Web/API standalone mode."""
    cfg = getattr(init, "bot_config", None) or {}
    allowed = cfg.get("allowed_user") if isinstance(cfg, dict) else None
    if isinstance(allowed, list) and allowed:
        allowed = allowed[0]
    try:
        return int(allowed)
    except (TypeError, ValueError):
        return 0


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts or ts <= 0:
        return "未知"
    if ts >= 253402200000:
        return "长期 / 永久"
    try:
        return datetime.fromtimestamp(int(ts), tz=TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _extract_quota_packages(quota_info: Optional[Dict]) -> List[Dict]:
    if not isinstance(quota_info, dict):
        return []
    now = int(time.time())
    items: List[Dict] = []
    for pkg in quota_info.get("package") or []:
        if not isinstance(pkg, dict):
            continue
        pkg_name = str(pkg.get("name") or "配额")
        for exp in pkg.get("expire_info") or []:
            if not isinstance(exp, dict) or not exp.get("expire_time"):
                continue
            try:
                exp_int = int(exp["expire_time"])
            except (TypeError, ValueError):
                continue
            remain_sec = exp_int - now
            if remain_sec > 0:
                days, rem = divmod(remain_sec, 86400)
                hours = rem // 3600
                countdown = f"约 {days}天{hours}小时后" if days > 0 else f"约 {hours}小时后"
            else:
                countdown = "已到期"
            items.append({
                "name": pkg_name,
                "surplus": exp.get("surplus"),
                "expire_time": exp_int,
                "expire_text": _fmt_ts(exp_int),
                "countdown": countdown,
            })
    return items


def get_categories_list() -> List[Dict]:
    categories = (init.bot_config or {}).get("category_folder") or []
    result: List[Dict] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        display = str(cat.get("display_name") or cat.get("name") or "未命名分类")
        subs = []
        for pm in cat.get("path_map") or []:
            if isinstance(pm, dict) and pm.get("path"):
                subs.append({
                    "name": str(pm.get("name") or display),
                    "path": str(pm.get("path")),
                })
        result.append({
            "name": str(cat.get("name") or display),
            "display_name": display,
            "path_map": subs,
        })
    return result


def get_status_payload() -> Dict:
    uid = get_effective_user_id()
    user_info = None
    quota_info = None
    if init.openapi_115 is not None:
        try:
            user_info = init.openapi_115.get_user_info()
            if isinstance(user_info, dict) and "user_name" in user_info:
                quota_info = init.openapi_115.get_quota_info()
        except Exception as e:
            if init.logger:
                init.logger.warn(f"Web 状态查询获取 115 信息异常: {e}")

    authorized = isinstance(user_info, dict) and "user_name" in user_info
    account = None
    storage = None
    if authorized:
        vip = user_info.get("vip_info") or {}
        rt_space = user_info.get("rt_space_info") or {}
        account = {
            "user_id": user_info.get("user_id"),
            "user_name": user_info.get("user_name"),
            "vip_level": vip.get("level_name") or "普通用户",
            "vip_expire": _fmt_ts(vip.get("expire")),
        }
        storage = {
            "total": rt_space.get("all_total", {}).get("size_format", "未知"),
            "used": rt_space.get("all_use", {}).get("size_format", "未知"),
            "remain": rt_space.get("all_remain", {}).get("size_format", "未知"),
            "total_bytes": rt_space.get("all_total", {}).get("size", 0),
            "used_bytes": rt_space.get("all_use", {}).get("size", 0),
            "remain_bytes": rt_space.get("all_remain", {}).get("size", 0),
        }

    quota = None
    if isinstance(quota_info, dict) and "count" in quota_info:
        total_q = int(quota_info.get("count") or 0)
        used_q = int(quota_info.get("used") or 0)
        surplus_q = int(quota_info.get("surplus", max(0, total_q - used_q)) or 0)
        quota = {
            "total": total_q,
            "used": used_q,
            "surplus": surplus_q,
            "max_size": quota_info.get("max_size"),
            "reset_packages": _extract_quota_packages(quota_info),
        }

    app_id = str((init.bot_config or {}).get("115_app_id") or "").strip()
    can_qr_auth = bool(app_id and app_id.lower() != "your_115_app_id")

    return {
        "version": BOT_VERSION,
        "server_time": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "authorized": authorized,
        "can_qr_auth": can_qr_auth,
        "account": account,
        "storage": storage,
        "quota": quota,
        "remembered_save_path": get_user_last_save_path(uid),
        "default_save_path": get_default_save_path(uid),
        "categories": get_categories_list(),
        "auto_retry_enabled": is_auto_retry_enabled(),
        "retry_max_wait_hours": get_retry_max_wait_hours(),
        "retry_max_wait_label": format_retry_cap_label(),
        "notify_tg_on_web_download": get_notify_tg_on_web_download(),
    }


def parse_uploaded_file_payload(filename: str, content_bytes: bytes, mime_type: str = "") -> Dict:
    parsed_pairs, first_non_link_line, doc_kind = parse_document_links(
        filename or "file.txt",
        content_bytes,
        mime_type=mime_type,
    )
    file_base, _ = get_document_base_and_ext(filename or "file.txt")
    ts_str = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M")
    base_folder = f"{file_base}{first_non_link_line}" if (doc_kind == "TXT" and first_non_link_line) else file_base
    suggested_folder = sanitize_folder_name(f"{base_folder}_{ts_str}")
    preview_filename = build_preview_txt_filename(file_base, len(parsed_pairs), ts_str)

    links = [
        {
            "index": idx,
            "kind": kind,
            "link": url,
            "display_name": extract_display_name_from_link(url, f"链接 #{idx}"),
        }
        for idx, (kind, url) in enumerate(parsed_pairs, 1)
    ]

    quota = None
    if init.openapi_115 is not None:
        try:
            q = init.openapi_115.get_quota_info()
            if isinstance(q, dict) and "count" in q:
                total_q = int(q.get("count") or 0)
                used_q = int(q.get("used") or 0)
                quota = {"total": total_q, "used": used_q, "surplus": max(0, total_q - used_q)}
        except Exception:
            pass

    return {
        "file_name": filename,
        "doc_kind": doc_kind,
        "link_count": len(links),
        "suggested_folder_name": suggested_folder,
        "preview_filename": preview_filename,
        "links": links,
        "quota": quota,
    }


def parse_raw_text_payload(raw_text: str) -> Dict:
    raw = (raw_text or "").strip()
    pairs = extract_links_from_text_chunk(raw) if raw else []
    suggested_folder = extract_folder_name_from_text(raw) if raw else ""
    links = [
        {
            "index": idx,
            "kind": kind,
            "link": url,
            "display_name": extract_display_name_from_link(url, f"链接 #{idx}"),
        }
        for idx, (kind, url) in enumerate(pairs, 1)
    ]
    return {
        "link_count": len(links),
        "suggested_folder_name": suggested_folder,
        "links": links,
    }


def _normalize_links_for_submit(links: Optional[List[str]], raw_text: Optional[str]) -> List[Dict]:
    items: List[Dict] = []
    seen = set()

    if links:
        for raw_link in links:
            u = str(raw_link or "").strip()
            if not u or u in seen:
                continue
            parsed = extract_links_from_text_chunk(u)
            if parsed:
                for kind, url in parsed:
                    if url not in seen:
                        seen.add(url)
                        items.append({"link": url, "type": _PARSER_KIND_TO_URL_TYPE.get(kind, DownloadUrlType.MAGNET)})
            else:
                kind = classify_link(u) or "magnet"
                seen.add(u)
                items.append({"link": u, "type": _PARSER_KIND_TO_URL_TYPE.get(kind, DownloadUrlType.MAGNET)})

    if raw_text and raw_text.strip():
        for kind, url in extract_links_from_text_chunk(raw_text):
            if url not in seen:
                seen.add(url)
                items.append({"link": url, "type": _PARSER_KIND_TO_URL_TYPE.get(kind, DownloadUrlType.MAGNET)})

    return items


def submit_download_payload(
    *,
    links: Optional[List[str]] = None,
    raw_text: Optional[str] = None,
    save_path: Optional[str] = None,
    custom_folder_name: Optional[str] = None,
    notify_tg: Optional[bool] = None,
    remember_path: bool = True,
) -> Dict:
    if init.openapi_115 is None:
        raise RuntimeError("115 客户端尚未初始化，请先在首页完成 115 扫码授权")

    normalized_links = _normalize_links_for_submit(links, raw_text)
    if not normalized_links:
        raise ValueError("未检测到有效的磁力 / 种子 / ed2k / 迅雷下载链接")

    uid = get_effective_user_id()
    target_path = (save_path or "").strip() or (get_default_save_path(uid) or "")
    if not target_path:
        raise ValueError("未指定保存路径，且系统尚未配置默认下载目录")

    folder = (custom_folder_name or "").strip()
    if not folder and raw_text and len(normalized_links) > 1:
        folder = extract_folder_name_from_text(raw_text) or ""
    if folder:
        folder = sanitize_folder_name(folder)

    if remember_path and target_path:
        remember_user_last_save_path(uid, target_path)

    effective_notify_tg = get_notify_tg_on_web_download() if notify_tg is None else bool(notify_tg)
    # 纯 Web 独立模式（无 TG 队列/无有效 user_id）自动降级为不发 TG，避免阻塞或报错
    if effective_notify_tg and uid <= 0:
        effective_notify_tg = False

    tracker_id = f"dl_{uuid.uuid4().hex[:8]}"
    download_executor.submit(
        download_tasks_batch,
        normalized_links,
        target_path,
        uid,
        folder,
        None,
        effective_notify_tg,
        tracker_id,
    )

    final_display = f"{target_path}/{folder}" if (folder and len(normalized_links) > 1) else target_path
    return {
        "ok": True,
        "tracker_id": tracker_id,
        "accepted_count": len(normalized_links),
        "save_path": target_path,
        "custom_folder_name": folder,
        "target_display_path": final_display,
        "notify_tg": effective_notify_tg,
    }


def list_unified_tasks_payload(filter_key: str = "all", limit: int = 100) -> Dict:
    fk = _norm_queue_filter(filter_key)
    mem_tasks = list_queue_tasks(limit=30)
    mem_running = [t for t in mem_tasks if t.get("status") == "running"]
    running_gids = {t.get("batch_group_id") for t in mem_running if t.get("batch_group_id")}
    mem_unlinked_finished = [
        t for t in mem_tasks if t.get("status") != "running" and not t.get("batch_group_id")
    ]
    groups = [g for g in _load_queue_groups(limit=limit) if g["group_id"] not in running_gids]

    entries: List[Dict] = []
    for t in mem_running:
        entries.append({
            "kind": "live",
            "id": t["task_id"],
            "batch_group_id": t.get("batch_group_id"),
            "category": "run",
            "priority": 0,
            "title": str(t.get("title") or "进行中任务"),
            "stage": str(t.get("stage") or "进行中"),
            "success": int(t.get("completed") or 0),
            "failed": int(t.get("failed") or 0),
            "dead": 0,
            "pending": max(0, int(t.get("total") or 1) - int(t.get("completed") or 0) - int(t.get("failed") or 0)),
            "total": int(t.get("total") or 1),
            "attempt": 1,
            "save_path": t.get("save_path") or "",
            "created_at": int(t.get("started_at") or time.time()),
            "created_text": _fmt_ts(int(t.get("started_at") or time.time())),
            "next_retry_at": None,
            "next_retry_text": None,
        })

    for g in groups:
        cat = g["category"]
        next_at = g.get("next_at")
        now_ts = int(time.time())
        next_text = None
        if cat == "retry":
            if not is_auto_retry_enabled():
                next_text = "自动重试已关闭"
            elif next_at and next_at > now_ts:
                next_text = f"{format_wait_duration(next_at - now_ts)}后 ({_fmt_ts(next_at)})"
            else:
                next_text = "即将执行"
        entries.append({
            "kind": "group",
            "id": g["group_id"],
            "batch_group_id": g["group_id"],
            "category": cat,
            "priority": {"run": 0, "retry": 1}.get(cat, 2),
            "title": g["display_name"],
            "stage": {"run": "进行中", "retry": "待重试", "done": "全部完成", "fail": "已失败"}[cat],
            "success": g["s"],
            "failed": g["f"],
            "dead": g["d"],
            "pending": g["p"],
            "total": g["total"],
            "attempt": g["attempt"],
            "save_path": "",
            "created_at": g.get("created_at"),
            "created_text": _fmt_ts(g.get("created_at")),
            "next_retry_at": next_at,
            "next_retry_text": next_text,
        })

    for t in mem_unlinked_finished:
        cat = "done" if t.get("status") == "completed" else "fail"
        entries.append({
            "kind": "live",
            "id": t["task_id"],
            "batch_group_id": None,
            "category": cat,
            "priority": 2,
            "title": str(t.get("title") or "任务"),
            "stage": str(t.get("stage") or ""),
            "success": int(t.get("completed") or 0),
            "failed": int(t.get("failed") or 0),
            "dead": 0,
            "pending": 0,
            "total": int(t.get("total") or 1),
            "attempt": 1,
            "save_path": t.get("save_path") or "",
            "created_at": int(t.get("started_at") or time.time()),
            "created_text": _fmt_ts(int(t.get("started_at") or time.time())),
            "next_retry_at": None,
            "next_retry_text": None,
        })

    entries.sort(key=lambda e: e["priority"])
    counts = {k: sum(1 for e in entries if e["category"] == k) for k in ("run", "retry", "done", "fail")}
    counts["all"] = len(entries)
    visible = [e for e in entries if fk == "all" or e["category"] == fk]

    return {
        "filter": fk,
        "counts": counts,
        "auto_retry_enabled": is_auto_retry_enabled(),
        "retry_max_wait_hours": get_retry_max_wait_hours(),
        "retry_max_wait_label": format_retry_cap_label(),
        "tasks": visible[:limit],
    }


def get_live_task_payload(task_id: str) -> Optional[Dict]:
    task = get_task(task_id)
    if not task:
        return None
    return {
        "kind": "live",
        "task_id": task["task_id"],
        "batch_group_id": task.get("batch_group_id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "stage": task.get("stage"),
        "total": task.get("total"),
        "completed": task.get("completed"),
        "failed": task.get("failed"),
        "save_path": task.get("save_path"),
        "started_at": task.get("started_at"),
        "updated_at": task.get("updated_at"),
        "finished_at": task.get("finished_at"),
        "items": task.get("items") or [],
    }


def get_group_detail_payload(group_id: str, page: int = 1, page_size: int = 50, mode: str = "all") -> Optional[Dict]:
    group_info = get_batch_task_group_info(group_id)
    if not group_info:
        return None
    items = get_batch_task_items(group_id)
    summary = summarize_group_items(items)
    display_name = get_group_display_name(group_info, items)
    full_path = get_group_actual_path(group_info)

    mode = "failed" if mode == "failed" else "all"
    filtered = [it for it in items if it.get("status") in ("failed", "dead", "pending")] if mode == "failed" else items
    page_size = max(1, min(200, int(page_size or 50)))
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), total_pages))
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    now_ts = int(time.time())
    next_at = summary["next_retry_at"]
    next_retry_text = None
    if summary["failed"]:
        if not is_auto_retry_enabled():
            next_retry_text = "自动重试已关闭（可点击立即重试或开启开关）"
        elif next_at and next_at > now_ts:
            next_retry_text = f"{format_wait_duration(next_at - now_ts)}后（{_fmt_ts(next_at)}）"
        else:
            next_retry_text = "即将执行"

    serialized_items = []
    for idx, it in enumerate(page_items, start + 1):
        name = format_retry_task_name(
            it.get("resource_name"),
            it.get("magnet") or "",
            created_at=it.get("created_at") or group_info.get("created_at"),
            link_count=1,
        )
        serialized_items.append({
            "index": idx,
            "id": it.get("id"),
            "name": name,
            "magnet": it.get("magnet") or "",
            "status": it.get("status"),
            "attempt": int(it.get("retry_count") or 0) + 1,
            "current_wait": it.get("current_wait"),
            "current_wait_text": format_wait_duration(it.get("current_wait")) if it.get("current_wait") else None,
            "next_retry_at": it.get("next_retry_at"),
            "next_retry_text": _fmt_ts(it.get("next_retry_at")) if it.get("next_retry_at") else None,
        })

    return {
        "kind": "group",
        "group_id": group_id,
        "display_name": display_name,
        "category": summary["category"],
        "save_path": full_path,
        "created_at": group_info.get("created_at"),
        "created_text": _fmt_ts(group_info.get("created_at")),
        "attempt": summary["attempt"],
        "summary": {
            "success": summary["success"],
            "failed": summary["failed"],
            "dead": summary["dead"],
            "pending": summary["pending"],
            "total": len(items) or int(group_info.get("total_count") or 0),
        },
        "auto_retry_enabled": is_auto_retry_enabled(),
        "next_retry_text": next_retry_text,
        "current_wait_text": format_wait_duration(summary["current_wait"]) if summary["current_wait"] else None,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "mode": mode,
        "items": serialized_items,
    }


def retry_group_payload(group_id: str) -> Dict:
    return retry_batch_group_now(group_id)


def delete_group_payload(group_id: str) -> Dict:
    delete_batch_task_group(group_id)
    return {"ok": True, "group_id": group_id}


def clear_groups_payload(category: str) -> Dict:
    if category not in ("done", "fail"):
        raise ValueError("category 必须为 done 或 fail")
    removed = clear_queue_groups(category)
    return {"ok": True, "category": category, "removed": removed}


def get_cloud_tasks_payload(page: int = 1) -> Dict:
    page = max(1, int(page or 1))
    tasks, total_count, page_count = _fetch_cloud_tasks_page(page=page)
    if tasks is None:
        return {
            "ok": False,
            "page": page,
            "page_count": 1,
            "total_count": 0,
            "tasks": [],
            "error": "无法获取 115 云端离线任务，请检查 115 授权状态",
        }
    items = []
    for t in tasks:
        st = t.get("status")
        pct = int(t.get("percentDone") or 0)
        if st == 2 or pct == 100:
            st_label = "已完成"
        elif st == -1:
            st_label = "失败"
        elif st == 1:
            st_label = f"下载中 {pct}%"
        else:
            st_label = "等待中"
        items.append({
            "info_hash": t.get("info_hash") or "",
            "name": str(t.get("name") or "未命名任务"),
            "status": st,
            "status_label": st_label,
            "percent": pct,
            "size": t.get("size"),
            "url": t.get("url") or "",
            "add_time": t.get("add_time"),
            "add_time_text": _fmt_ts(t.get("add_time")) if t.get("add_time") else None,
        })
    return {
        "ok": True,
        "page": page,
        "page_count": page_count,
        "total_count": total_count,
        "tasks": items,
    }


def delete_cloud_task_payload(info_hash: str, delete_source: bool = False) -> Dict:
    if init.openapi_115 is None:
        raise RuntimeError("115 客户端尚未初始化")
    ok = bool(init.openapi_115.del_faild_offline_task(info_hash, delete_source))
    return {"ok": ok, "info_hash": info_hash, "delete_source": delete_source}


def clear_cloud_tasks_payload(flag: int) -> Dict:
    if flag not in (1, 2, 4):
        raise ValueError("flag 必须为 1(全部记录)、2(失败任务) 或 4(已完成任务)")
    if init.openapi_115 is None:
        raise RuntimeError("115 客户端尚未初始化")
    ok = bool(init.openapi_115.clear_cloud_task(flag))
    return {"ok": ok, "flag": flag}


def get_settings_payload() -> Dict:
    uid = get_effective_user_id()
    cap_hours = get_retry_max_wait_hours()
    chain, attempt_count = build_retry_wait_chain_str(cap_hours)
    return {
        "username": get_web_username(),
        "api_key": get_api_key(),
        "auto_retry_enabled": is_auto_retry_enabled(),
        "retry_max_wait_hours": cap_hours,
        "retry_max_wait_label": format_retry_cap_label(cap_hours),
        "retry_cap_options": [
            {"hours": h, "label": format_retry_cap_label(h)} for h in RETRY_CAP_OPTIONS_HOURS
        ],
        "retry_wait_chain": chain,
        "retry_attempt_count": attempt_count,
        "notify_tg_on_web_download": get_notify_tg_on_web_download(),
        "remembered_save_path": get_user_last_save_path(uid),
        "default_save_path": get_default_save_path(uid),
        "panel_refresh_debounce_sec": get_panel_refresh_debounce_sec(),
        "file_confirm_auto_timeout_sec": get_file_confirm_auto_timeout_sec(),
        "categories": get_categories_list(),
    }


def update_settings_payload(
    *,
    auto_retry_enabled: Optional[bool] = None,
    retry_max_wait_hours: Optional[int] = None,
    notify_tg_on_web_download: Optional[bool] = None,
    default_save_path: Optional[str] = None,
) -> Dict:
    guide_html: Optional[str] = None
    prev_auto_retry = is_auto_retry_enabled()

    if auto_retry_enabled is not None:
        new_flag = set_auto_retry_enabled(bool(auto_retry_enabled))
        if new_flag and not prev_auto_retry:
            guide_html = build_auto_retry_guide_text()

    if retry_max_wait_hours is not None:
        set_retry_max_wait_hours(retry_max_wait_hours)

    if notify_tg_on_web_download is not None:
        set_notify_tg_on_web_download(bool(notify_tg_on_web_download))

    if default_save_path is not None:
        uid = get_effective_user_id()
        cleaned = default_save_path.strip()
        if cleaned:
            remember_user_last_save_path(uid, cleaned)
        else:
            clear_user_last_save_path(uid)

    payload = get_settings_payload()
    payload["auto_retry_guide_html"] = guide_html
    return payload


def reset_default_path_payload() -> Dict:
    uid = get_effective_user_id()
    cleared = clear_user_last_save_path(uid)
    return {
        "ok": True,
        "cleared": cleared,
        "default_save_path": get_default_save_path(uid),
    }


def reload_config_payload() -> Dict:
    init.load_yaml_config()
    if init.logger:
        init.logger.info("Reload configuration success (via Web/API)")
    return {"ok": True, "message": "配置已重载完成"}


def get_log_file_path() -> str:
    return os.path.join(init.CONFIG, "115bot.log")


def read_log_tail(lines: int = 200) -> Dict:
    lines = max(10, min(2000, int(lines or 200)))
    log_path = get_log_file_path()
    if not os.path.exists(log_path):
        return {"exists": False, "file_size": 0, "lines": []}
    file_size = os.path.getsize(log_path)
    buf = deque(maxlen=lines)
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            buf.append(line.rstrip("\r\n"))
    return {
        "exists": True,
        "file_size": file_size,
        "lines": list(buf),
    }
