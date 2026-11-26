# -*- coding: utf-8 -*-
"""Thread-safe in-memory tracker for active/recent download & upload tasks with per-item magnet details."""

import copy
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import unquote, parse_qs, urlparse

TZ_CN = timezone(timedelta(hours=8))
_PLACEHOLDER_NAME_RE = re.compile(
    r"^(未知资源(_\d+)?|未知任务|未知链接|未命名(任务)?|unknown|\d{8}\s+\d{6}\+\d+V)$",
    re.IGNORECASE,
)


def extract_real_filename_from_link(link: str) -> str:
    """Only extract an explicit filename from ed2k, magnet dn=, or .torrent URL path (not a bare BTIH hash)."""
    if not link:
        return ""
    lower = link.strip().lower()
    if lower.startswith("ed2k://"):
        parts = link.strip().split("|")
        if len(parts) >= 3 and parts[2].strip():
            return unquote(parts[2].strip())
    elif lower.startswith("magnet:?"):
        try:
            qs = parse_qs(urlparse(link.strip()).query)
            dn = qs.get("dn")
            if dn and dn[0].strip():
                return unquote(dn[0].strip())
        except Exception:
            pass
    elif lower.startswith(("http://", "https://")):
        try:
            path = urlparse(link.strip()).path or ""
            base = path.rsplit("/", 1)[-1].strip()
            if base.lower().endswith(".torrent"):
                return unquote(base)
        except Exception:
            pass
    return ""


def is_valid_extracted_filename(name: Optional[str]) -> bool:
    """Return True if name is a real extracted filename rather than empty/placeholder/raw link."""
    if not name:
        return False
    s = str(name).strip()
    if not s or _PLACEHOLDER_NAME_RE.match(s):
        return False
    if s.lower().startswith(("magnet:?", "ed2k://", "thunder://", "http://", "https://")):
        return False
    return True


def _format_created_time_compact(created_at=None) -> str:
    """Format created_at (Unix ts, SQLite UTC string, or datetime) to UTC+8 yyyyMMDDHHmmss."""
    dt = None
    if isinstance(created_at, datetime):
        dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    elif isinstance(created_at, (int, float)) and created_at > 0:
        dt = datetime.fromtimestamp(created_at, tz=TZ_CN)
    elif isinstance(created_at, str) and created_at.strip():
        raw = created_at.strip()
        if raw.isdigit():
            dt = datetime.fromtimestamp(int(raw), tz=TZ_CN)
        else:
            for fmt, tz_default in (
                ("%Y-%m-%d %H:%M:%S", timezone.utc),
                ("%Y-%m-%dT%H:%M:%S", timezone.utc),
                ("%Y%m%d%H%M%S", TZ_CN),
            ):
                try:
                    dt = datetime.strptime(raw[:19], fmt).replace(tzinfo=tz_default)
                    break
                except ValueError:
                    continue
    if dt is None:
        dt = datetime.now(TZ_CN)
    return dt.astimezone(TZ_CN).strftime("%Y%m%d%H%M%S")


_LINK_KIND_LABELS = {"magnet": "磁力", "ed2k": "ed2k", "thunder": "迅雷", "torrent": "种子"}


def _link_kind(link: str) -> str:
    lower = (link or "").strip().lower()
    if lower.startswith("magnet:?"):
        return "magnet"
    if lower.startswith("ed2k://"):
        return "ed2k"
    if lower.startswith("thunder://"):
        return "thunder"
    if lower.startswith(("http://", "https://")) and ".torrent" in lower:
        return "torrent"
    return "other"


def describe_link_kind(links=None, kinds=None) -> str:
    """Return a readable task kind label: 磁力 / ed2k / 迅雷 (or 链接 when mixed/unknown)."""
    kind_set = set(kinds or [])
    if not kind_set:
        kind_set = {_link_kind(u) for u in (links or []) if u}
    kind_set.discard("")
    if len(kind_set) == 1:
        return _LINK_KIND_LABELS.get(next(iter(kind_set)), "链接")
    return "链接"


def format_retry_task_name(
    extracted_name: Optional[str],
    links,
    *,
    created_at=None,
    link_count: Optional[int] = None,
    link_kinds=None,
) -> str:
    """
    Build display/file name for tasks:
    - Prefer a real extracted filename (from 115 or link dn=/ed2k filename).
    - Single link without filename: `{yyyyMMDDHHmmss}-{magnet}`.
    - 2+ links without a real name: `{yyyyMMDDHHmmss}+{N}个磁力` (kind = 磁力/ed2k/迅雷/链接) for easy recognition.
    """
    if is_valid_extracted_filename(extracted_name):
        return str(extracted_name).strip()

    if isinstance(links, str):
        link_list = [u.strip() for u in links.splitlines() if u.strip()]
    elif isinstance(links, (list, tuple)):
        link_list = []
        for u in links:
            val = (u.get("link") or u.get("magnet")) if isinstance(u, dict) else str(u)
            if val and str(val).strip():
                link_list.append(str(val).strip())
    else:
        link_list = []

    count = link_count if link_count is not None else len(link_list)
    first_link = link_list[0] if link_list else ""

    if count <= 1 and first_link:
        from_link = extract_real_filename_from_link(first_link)
        if is_valid_extracted_filename(from_link):
            return from_link

    ts_str = _format_created_time_compact(created_at)
    if count >= 2:
        return f"{ts_str}+{count}个{describe_link_kind(link_list, link_kinds)}"
    if first_link:
        return f"{ts_str}-{first_link}"
    return ts_str


def is_fallback_retry_name(name: Optional[str]) -> bool:
    """Check whether name is a generated fallback (`yyyyMMDDHHmmss-<link>` or `yyyyMMDDHHmmss+N个磁力`)."""
    if not name:
        return False
    s = str(name).strip()
    return bool(
        re.match(r"^\d{14}-(magnet:\?|ed2k://|thunder://|https?://)", s, re.IGNORECASE)
        or re.match(r"^\d{14}\+\d+个(磁力|ed2k|迅雷|种子|链接)$", s)
    )


_lock = threading.Lock()
_active_tasks: Dict[str, Dict] = {}
_recent_tasks = deque(maxlen=15)


def extract_display_name_from_link(link: str, fallback: str = "") -> str:
    """Extract a readable resource name from magnet/ed2k/thunder URL for task detail display."""
    if not link:
        return fallback or "未知链接"
    lower = link.lower()
    if lower.startswith("ed2k://"):
        parts = link.split("|")
        if len(parts) >= 3 and parts[2]:
            return unquote(parts[2])
    elif lower.startswith("magnet:?"):
        try:
            qs = parse_qs(urlparse(link).query)
            dn = qs.get("dn")
            if dn and dn[0]:
                return unquote(dn[0])
            xt = qs.get("xt")
            if xt and xt[0]:
                return xt[0].split(":")[-1]
        except Exception:
            pass
    return fallback or (link[:48] + "..." if len(link) > 48 else link)


def _normalize_items(items: Optional[List[Dict]]) -> List[Dict]:
    result: List[Dict] = []
    if not items:
        return result
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            link = str(item.get("link") or "")
            item_index = int(item.get("index") or idx)
            name = str(item.get("name") or extract_display_name_from_link(link, f"任务 #{item_index}"))
            result.append({
                "index": item_index,
                "link": link,
                "name": name,
                "status": str(item.get("status") or "pending"),  # pending|submitted|running|success|failed
                "percent": int(item.get("percent") or 0),
                "error": str(item.get("error") or ""),
            })
    return result


def register_task(
    task_id: str,
    task_type: str,
    title: str,
    total: int = 1,
    save_path: str = "",
    user_id: Optional[int] = None,
    stage: str = "准备中",
    items: Optional[List[Dict]] = None,
    chat_id: Optional[int] = None,
    origin_message_id: Optional[int] = None,
) -> Dict:
    """Register a running download/upload task along with optional per-magnet item list."""
    now = time.time()
    normalized_items = _normalize_items(items)
    entry = {
        "task_id": task_id,
        "task_type": task_type,  # "download" | "upload"
        "title": title or "未命名任务",
        "total": total or len(normalized_items) or 1,
        "completed": 0,
        "failed": 0,
        "save_path": save_path,
        "user_id": user_id,
        "chat_id": chat_id,
        "origin_message_id": origin_message_id,
        "stage": stage,
        "status": "running",
        "items": normalized_items,
        "started_at": now,
        "updated_at": now,
    }
    with _lock:
        _active_tasks[task_id] = entry
    return copy.deepcopy(entry)


def update_task(task_id: str, **fields) -> Optional[Dict]:
    """Update top-level progress fields of an active task."""
    with _lock:
        entry = _active_tasks.get(task_id)
        if not entry:
            return None
        entry.update(fields)
        entry["updated_at"] = time.time()
        return copy.deepcopy(entry)


def update_task_item(
    task_id: str,
    index: int,
    *,
    name: Optional[str] = None,
    status: Optional[str] = None,
    percent: Optional[int] = None,
    error: Optional[str] = None,
) -> Optional[Dict]:
    """Update an individual magnet/file item inside a tracked task and sync aggregate counts."""
    with _lock:
        entry = _active_tasks.get(task_id)
        if not entry:
            return None
        items = entry.get("items") or []
        target = None
        for item in items:
            if item.get("index") == index:
                target = item
                break
        if target is None and 1 <= index <= len(items):
            target = items[index - 1]
        if target is None:
            return None

        if name:
            target["name"] = str(name)
        if status is not None:
            target["status"] = str(status)
        if percent is not None:
            try:
                target["percent"] = max(0, min(100, int(percent)))
            except (TypeError, ValueError):
                pass
        if error is not None:
            target["error"] = str(error)

        entry["completed"] = sum(1 for it in items if it.get("status") == "success")
        entry["failed"] = sum(1 for it in items if it.get("status") == "failed")
        entry["updated_at"] = time.time()
        return copy.deepcopy(target)


def finish_task(task_id: str, status: str = "completed", note: str = "") -> Optional[Dict]:
    """Mark an active task finished and move it into recent history (preserving item details)."""
    with _lock:
        entry = _active_tasks.pop(task_id, None)
        if not entry:
            return None
        entry["status"] = status
        entry["stage"] = note or ("已完成" if status == "completed" else "失败")
        entry["finished_at"] = time.time()
        entry["updated_at"] = entry["finished_at"]
        _recent_tasks.appendleft(entry)
        return copy.deepcopy(entry)


def get_task(task_id: str) -> Optional[Dict]:
    """Look up a task (active or recently finished) by task_id."""
    with _lock:
        if task_id in _active_tasks:
            return copy.deepcopy(_active_tasks[task_id])
        for entry in _recent_tasks:
            if entry.get("task_id") == task_id:
                return copy.deepcopy(entry)
    return None


def get_active_tasks() -> List[Dict]:
    """Return snapshots of all currently active tasks."""
    with _lock:
        return [copy.deepcopy(v) for v in _active_tasks.values()]


def get_recent_tasks(limit: int = 5) -> List[Dict]:
    """Return snapshots of recently finished tasks."""
    with _lock:
        return [copy.deepcopy(v) for v in list(_recent_tasks)[:limit]]


def list_queue_tasks(limit: int = 10) -> List[Dict]:
    """Return active tasks followed by recent tasks for the interactive queue panel."""
    with _lock:
        combined = [copy.deepcopy(v) for v in _active_tasks.values()]
        remaining = max(0, limit - len(combined))
        if remaining > 0:
            combined.extend(copy.deepcopy(v) for v in list(_recent_tasks)[:remaining])
        return combined


def clear_tasks() -> None:
    """Reset tracker state (useful for unit tests)."""
    with _lock:
        _active_tasks.clear()
        _recent_tasks.clear()
