# -*- coding: utf-8 -*-

"""
批量任务管理模块
负责批量下载任务的创建、追踪、重试管理
"""

import init
import uuid
import time
from datetime import datetime, timedelta
from app.utils.sqlitelib import SqlLiteLib
from typing import List, Dict, Optional


def initialize_batch_task_tables(reset_sql=False):
    """
    初始化批量任务相关数据库表

    Args:
        reset_sql: 是否重置数据库表（默认False，仅当需要清空数据时设为True）
    """
    with SqlLiteLib() as sqlite:
        # 只有在 reset_sql=True 时才删除旧表
        if reset_sql:
            sqlite.execute_sql("DROP TABLE IF EXISTS batch_task_group")
            sqlite.execute_sql("DROP TABLE IF EXISTS batch_task_item")
            init.logger.info("🗑️ 已清理旧的批量任务数据库表（RESET_SQL=True）")

        # 批量任务组表（使用整数时间戳，记录原始消息ID以便后台重试完成后引用）
        create_group_table = """
        CREATE TABLE IF NOT EXISTS batch_task_group (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT UNIQUE NOT NULL,
            folder_name TEXT NOT NULL,
            save_path TEXT NOT NULL,
            total_count INTEGER NOT NULL,
            success_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            chat_id INTEGER,
            origin_message_id INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
        sqlite.execute_sql(create_group_table)

        # 兼容旧库：若缺少 chat_id / origin_message_id 字段则自动迁移补列
        existing_cols = {
            row.get("name") for row in (sqlite.query_all("PRAGMA table_info(batch_task_group)") or [])
            if isinstance(row, dict)
        }
        if "chat_id" not in existing_cols:
            sqlite.execute_sql("ALTER TABLE batch_task_group ADD COLUMN chat_id INTEGER")
        if "origin_message_id" not in existing_cols:
            sqlite.execute_sql("ALTER TABLE batch_task_group ADD COLUMN origin_message_id INTEGER")

        # 批量任务项表（使用整数时间戳）
        create_item_table = """
        CREATE TABLE IF NOT EXISTS batch_task_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            magnet TEXT NOT NULL,
            resource_name TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            max_retry INTEGER DEFAULT 5,
            last_retry_at INTEGER,
            next_retry_at INTEGER,
            completed_at INTEGER,
            created_at INTEGER NOT NULL
        )
        """
        sqlite.execute_sql(create_item_table)

        # 兼容旧库：补充「本轮等待时长」字段（用于 2 倍指数退避重试）
        item_cols = {
            row.get("name") for row in (sqlite.query_all("PRAGMA table_info(batch_task_item)") or [])
            if isinstance(row, dict)
        }
        if "current_wait" not in item_cols:
            sqlite.execute_sql("ALTER TABLE batch_task_item ADD COLUMN current_wait INTEGER")

        # 面板可配置项存储表（如重试最长等待上限）
        from app.utils.bot_settings import ensure_settings_table
        ensure_settings_table(sqlite)

        if reset_sql:
            init.logger.info("✅ 批量任务数据库表已重置（使用时间戳格式）")
        else:
            init.logger.info("✅ 批量任务数据库表初始化完成（使用时间戳格式）")

    # 将旧版 /rl 重试列表（offline_task）迁移到统一任务组
    try:
        migrated = migrate_legacy_offline_tasks()
        if migrated:
            init.logger.info(f"📦 已将 {migrated} 条旧版 /rl 重试记录迁移到统一任务队列")
    except Exception as e:
        init.logger.warn(f"迁移旧版重试记录失败: {e}")


def generate_batch_folder_name(task_count: int) -> str:
    """
    生成默认批量任务文件夹名
    格式: {yyyyMMdd HHmmss}+{任务数量}V

    Args:
        task_count: 任务数量

    Returns:
        文件夹名称，例如: "20251011 143025+5V"
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d %H%M%S")
    folder_name = f"{timestamp}+{task_count}V"
    return folder_name


def create_batch_task_group(
    folder_name: str,
    save_path: str,
    total_count: int,
    chat_id: Optional[int] = None,
    origin_message_id: Optional[int] = None,
    created_at: Optional[int] = None,
) -> str:
    """
    创建任务组（批量或单链接）

    Args:
        folder_name: 文件夹名称（单链接任务为空字符串）
        save_path: 保存路径
        total_count: 总任务数
        chat_id: 用户聊天ID（用于后台重试通知）
        origin_message_id: 原始添加任务的消息ID（用于引用回复）
        created_at: 任务添加时间（默认当前时间）

    Returns:
        group_id: 任务组唯一标识
    """
    group_id = str(uuid.uuid4())[:8]  # 使用8位UUID作为标识
    current_timestamp = int(time.time())
    created_timestamp = int(created_at) if created_at else current_timestamp

    with SqlLiteLib() as sqlite:
        sql = """
        INSERT INTO batch_task_group
        (group_id, folder_name, save_path, total_count, status, chat_id, origin_message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """
        sqlite.execute_sql(
            sql,
            (group_id, folder_name or "", save_path, total_count, chat_id, origin_message_id, created_timestamp, current_timestamp),
        )

    init.logger.info(f"📦 创建批量任务组: {group_id}, 文件夹: {folder_name}, 任务数: {total_count}")
    return group_id


def save_batch_task_items(group_id: str, links: List[Dict]) -> None:
    """
    保存批量任务项

    Args:
        group_id: 任务组ID
        links: 链接列表，格式: [{"link": "magnet:...", "type": DownloadUrlType}, ...]
    """
    current_timestamp = int(time.time())
    with SqlLiteLib() as sqlite:
        for link_info in links:
            magnet = link_info.get("link", "")
            if not magnet:
                continue

            # 对话内首次尝试的等待时长作为后续翻倍的基数
            from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS
            next_retry_timestamp = current_timestamp

            sql = """
            INSERT INTO batch_task_item
            (group_id, magnet, status, retry_count, current_wait, next_retry_at, created_at)
            VALUES (?, ?, 'pending', 0, ?, ?, ?)
            """
            sqlite.execute_sql(sql, (group_id, magnet, BASE_RETRY_WAIT_SECONDS, next_retry_timestamp, current_timestamp))

    init.logger.info(f"💾 已保存 {len(links)} 个批量任务项到组 {group_id}")


def update_task_item_status(magnet: str, group_id: str, status: str, resource_name: Optional[str] = None) -> None:
    """
    更新批量任务项状态

    Args:
        magnet: 磁力链接
        group_id: 任务组ID
        status: 状态 (success/failed/pending)
        resource_name: 资源名称（可选）
    """
    with SqlLiteLib() as sqlite:
        if status == 'success':
            # 成功任务：更新状态、资源名称、完成时间（使用Unix时间戳）
            completed_timestamp = int(time.time())
            sql = """
            UPDATE batch_task_item
            SET status = ?, resource_name = COALESCE(NULLIF(?, ''), resource_name), completed_at = ?
            WHERE magnet = ? AND group_id = ?
            """
            sqlite.execute_sql(sql, (status, resource_name or "", completed_timestamp, magnet, group_id))
        elif status == 'failed':
            # 失败任务：进入待重试；若尚未进行过后台重试，则下一轮调度立即重新提交（等待时间从基数开始翻倍）
            from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS
            now_ts = int(time.time())
            sql = """
            UPDATE batch_task_item
            SET status = ?,
                resource_name = COALESCE(NULLIF(?, ''), resource_name),
                current_wait = COALESCE(current_wait, ?),
                next_retry_at = CASE WHEN last_retry_at IS NULL THEN ? ELSE next_retry_at END
            WHERE magnet = ? AND group_id = ?
            """
            sqlite.execute_sql(sql, (status, resource_name or "", BASE_RETRY_WAIT_SECONDS, now_ts, magnet, group_id))
        else:
            sql = """
            UPDATE batch_task_item
            SET status = ?
            WHERE magnet = ? AND group_id = ?
            """
            sqlite.execute_sql(sql, (status, magnet, group_id))


def update_task_group_counts(group_id: str) -> None:
    """
    更新批量任务组的成功/失败计数

    Args:
        group_id: 任务组ID
    """
    with SqlLiteLib() as sqlite:
        # 统计成功和失败任务数
        success_sql = """
        SELECT COUNT(*) FROM batch_task_item
        WHERE group_id = ? AND status = 'success'
        """
        success_count = sqlite.query_one(success_sql, (group_id,)) or 0

        failed_sql = """
        SELECT COUNT(*) FROM batch_task_item
        WHERE group_id = ? AND (status = 'failed' OR status = 'dead')
        """
        failed_count = sqlite.query_one(failed_sql, (group_id,)) or 0

        # 更新任务组统计
        current_timestamp = int(time.time())
        update_sql = """
        UPDATE batch_task_group
        SET success_count = ?, failed_count = ?, updated_at = ?
        WHERE group_id = ?
        """
        sqlite.execute_sql(update_sql, (success_count, failed_count, current_timestamp, group_id))

        # 检查是否所有任务都已完成
        total_sql = "SELECT total_count FROM batch_task_group WHERE group_id = ?"
        total_count = sqlite.query_one(total_sql, (group_id,)) or 0

        if success_count + failed_count >= total_count:
            # 所有任务都已处理完成
            if failed_count == 0:
                # 全部成功
                status_sql = "UPDATE batch_task_group SET status = 'completed' WHERE group_id = ?"
                sqlite.execute_sql(status_sql, (group_id,))
                init.logger.info(f"✅ 批量任务组 {group_id} 已全部完成")
            else:
                # 有失败任务，标记为部分完成
                status_sql = "UPDATE batch_task_group SET status = 'partial' WHERE group_id = ?"
                sqlite.execute_sql(status_sql, (group_id,))
                init.logger.info(f"⚠️ 批量任务组 {group_id} 部分完成 (成功:{success_count}, 失败:{failed_count})")


def calculate_next_retry_time(retry_count: int) -> int:
    """
    计算下次检查时间（2 倍指数退避）：
    第 n 轮后台重试等待 = 基数(10分钟) × 2^n，且不超过面板配置的最长等待上限（默认 24 小时）。

    Args:
        retry_count: 当前已进行的后台重试次数

    Returns:
        下次检查时间（Unix时间戳）
    """
    from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS, get_retry_max_wait_seconds
    wait = min(BASE_RETRY_WAIT_SECONDS * (2 ** (max(0, int(retry_count)) + 1)), get_retry_max_wait_seconds())
    return int(time.time()) + int(wait)


def get_pending_retry_tasks() -> List[Dict]:
    """
    获取已到期的待重试任务（next_retry_at <= now）

    Returns:
        待重试任务列表
    """
    current_timestamp = int(time.time())

    with SqlLiteLib() as sqlite:
        sql = """
        SELECT * FROM batch_task_item
        WHERE status = 'failed'
        AND COALESCE(next_retry_at, 0) <= ?
        ORDER BY next_retry_at ASC
        LIMIT 100
        """
        tasks = sqlite.query_all(sql, (current_timestamp,))
        return tasks if tasks else []


def schedule_retry_attempt(item_id: int, retry_count: int, wait_seconds: int, now: Optional[int] = None) -> None:
    """记录一次重新提交：更新尝试次数、本轮等待时长与下次检查时间。"""
    now_ts = int(now or time.time())
    with SqlLiteLib() as sqlite:
        sqlite.execute_sql(
            """
            UPDATE batch_task_item
            SET status = 'failed', retry_count = ?, current_wait = ?, last_retry_at = ?, next_retry_at = ?
            WHERE id = ?
            """,
            (int(retry_count), int(wait_seconds), now_ts, now_ts + int(wait_seconds), item_id),
        )


def defer_retry_attempt(item_id: int, delay_seconds: int) -> None:
    """提交失败时推迟下次检查（不计入尝试次数）。"""
    with SqlLiteLib() as sqlite:
        sqlite.execute_sql(
            "UPDATE batch_task_item SET next_retry_at = ? WHERE id = ?",
            (int(time.time()) + int(delay_seconds), item_id),
        )


def mark_pending_items_failed(group_id: str) -> int:
    """对话内跟踪结束后仍处于 pending 的项（提交失败/查询异常）转入待重试。"""
    from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS
    with SqlLiteLib() as sqlite:
        count = sqlite.query_one(
            "SELECT COUNT(*) FROM batch_task_item WHERE group_id = ? AND status = 'pending'",
            (group_id,),
        ) or 0
        if count:
            sqlite.execute_sql(
                """
                UPDATE batch_task_item
                SET status = 'failed', current_wait = COALESCE(current_wait, ?), next_retry_at = ?
                WHERE group_id = ? AND status = 'pending'
                """,
                (BASE_RETRY_WAIT_SECONDS, int(time.time()), group_id),
            )
    if count:
        update_task_group_counts(group_id)
    return int(count)


def update_retry_attempt(task_id: int, retry_count: int) -> None:
    """兼容旧接口：按 2 倍退避规则更新重试尝试信息"""
    from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS, get_retry_max_wait_seconds
    wait = min(BASE_RETRY_WAIT_SECONDS * (2 ** max(1, int(retry_count))), get_retry_max_wait_seconds())
    schedule_retry_attempt(task_id, retry_count, wait)


def mark_task_as_dead(task_id: int) -> None:
    """
    标记任务为死任务（5次重试后仍失败）

    Args:
        task_id: 任务ID
    """
    with SqlLiteLib() as sqlite:
        sql = """
        UPDATE batch_task_item
        SET status = 'dead'
        WHERE id = ?
        """
        sqlite.execute_sql(sql, (task_id,))

    init.logger.warn(f"☠️ 任务 {task_id} 已标记为死任务（5次重试后仍失败）")


def generate_magnet_txt_file(group_id: str, folder_path: str, folder_name: str) -> bool:
    """
    生成磁力链接TXT文件

    Args:
        group_id: 任务组ID
        folder_path: 文件夹路径（115云盘路径）
        folder_name: 文件夹名称

    Returns:
        是否成功生成
    """
    import os
    from app.core.open_115 import file_sha1

    temp_txt_path = None
    try:
        # 从数据库获取所有任务的磁力链接
        with SqlLiteLib() as sqlite:
            sql = """
            SELECT magnet, resource_name, status
            FROM batch_task_item
            WHERE group_id = ?
            ORDER BY id ASC
            """
            tasks = sqlite.query_all(sql, (group_id,))

        if not tasks:
            init.logger.warn(f"任务组 {group_id} 没有找到任务项")
            return False

        # 构建TXT内容
        txt_content_lines = []
        txt_content_lines.append(f"批量任务组: {group_id}")
        txt_content_lines.append(f"文件夹: {folder_name}")
        txt_content_lines.append(f"总任务数: {len(tasks)}")
        txt_content_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        txt_content_lines.append("=" * 80)
        txt_content_lines.append("")

        for idx, task in enumerate(tasks, 1):
            status_emoji = "✅" if task['status'] == 'success' else "❌"
            resource_name = task.get('resource_name') or '未知资源'
            txt_content_lines.append(f"{idx}. {status_emoji} {resource_name}")
            txt_content_lines.append(f"   {task['magnet']}")
            txt_content_lines.append("")

        txt_content = "\n".join(txt_content_lines)

        # 使用115 API上传TXT文件
        safe_folder_name = (folder_name or f"batch_{group_id}").replace("/", "-").replace("\\", "-").strip()
        if not safe_folder_name:
            safe_folder_name = f"batch_{group_id}"
        txt_filename = f"{safe_folder_name}.txt"

        # 确保临时目录存在后再写入本地临时文件
        temp_dir = getattr(init, "TEMP", None) or "/tmp"
        os.makedirs(temp_dir, exist_ok=True)
        temp_txt_path = os.path.join(temp_dir, f"{group_id}_{txt_filename}")
        with open(temp_txt_path, 'w', encoding='utf-8') as f:
            f.write(txt_content)

        file_size = os.path.getsize(temp_txt_path)
        sha1_value = file_sha1(temp_txt_path)

        # 上传到115云盘（OpenAPI_115.upload_file 仅接受关键字参数并返回 (is_upload, bingo)）
        upload_result = init.openapi_115.upload_file(
            target=folder_path,
            file_name=txt_filename,
            file_size=file_size,
            fileid=sha1_value,
            file_path=temp_txt_path,
            request_times=1,
        )
        is_uploaded = bool(upload_result[0]) if isinstance(upload_result, tuple) else bool(upload_result)

        if is_uploaded:
            init.logger.info(f"📄 已生成磁力链接TXT文件: {folder_path}/{txt_filename}")
            return True
        else:
            init.logger.error(f"❌ 上传TXT文件失败: {txt_filename}")
            return False

    except Exception as e:
        init.logger.error(f"生成磁力链接TXT文件失败: {e}")
        return False
    finally:
        if temp_txt_path and os.path.exists(temp_txt_path):
            try:
                os.remove(temp_txt_path)
            except Exception as rm_err:
                init.logger.warn(f"清理临时TXT文件失败: {rm_err}")


def get_batch_task_group_info(group_id: str) -> Optional[Dict]:
    """
    获取批量任务组信息

    Args:
        group_id: 任务组ID

    Returns:
        任务组信息字典，如果不存在返回None
    """
    with SqlLiteLib() as sqlite:
        sql = "SELECT * FROM batch_task_group WHERE group_id = ?"
        result = sqlite.query_all(sql, (group_id,))
        return result[0] if result else None


def get_dead_task_groups() -> List[Dict]:
    """
    获取包含死任务的任务组

    Returns:
        包含死任务的任务组列表
    """
    with SqlLiteLib() as sqlite:
        sql = """
        SELECT DISTINCT btg.*
        FROM batch_task_group btg
        INNER JOIN batch_task_item bti ON btg.group_id = bti.group_id
        WHERE bti.status = 'dead'
        """
        return sqlite.query_all(sql)


def get_recent_batch_task_groups(limit: int = 8) -> List[Dict]:
    """获取最近的批量任务组（供对话任务队列持久化展示，重启后仍可点开明细）"""
    with SqlLiteLib() as sqlite:
        sql = """
        SELECT * FROM batch_task_group
        ORDER BY created_at DESC
        LIMIT ?
        """
        rows = sqlite.query_all(sql, (limit,))
        return rows if rows else []


def get_batch_task_items(group_id: str) -> List[Dict]:
    """获取指定批量任务组下的全部磁力任务项"""
    with SqlLiteLib() as sqlite:
        sql = """
        SELECT * FROM batch_task_item
        WHERE group_id = ?
        ORDER BY id ASC
        """
        rows = sqlite.query_all(sql, (group_id,))
        return rows if rows else []


def delete_batch_task_group(group_id: str) -> bool:
    """删除指定批量任务组及其任务项记录（不删除已下载网盘文件）"""
    with SqlLiteLib() as sqlite:
        sqlite.execute_sql("DELETE FROM batch_task_item WHERE group_id = ?", (group_id,))
        sqlite.execute_sql("DELETE FROM batch_task_group WHERE group_id = ?", (group_id,))
    init.logger.info(f"🗑 已删除批量任务组记录: {group_id}")
    return True


def get_group_actual_path(group_info: Optional[Dict]) -> str:
    """任务组实际保存目录：批量任务为 save_path/folder_name，单链接任务为 save_path。"""
    if not group_info:
        return ""
    save_path = (group_info.get("save_path") or "").rstrip("/") or (group_info.get("save_path") or "")
    folder_name = group_info.get("folder_name") or ""
    if save_path and folder_name:
        return f"{save_path}/{folder_name}"
    return save_path or folder_name


def get_group_display_name(group_info: Optional[Dict], items: Optional[List[Dict]] = None) -> str:
    """任务组展示名：优先文件夹名/提取到的文件名，否则使用 yyyyMMDDHHmmss-磁力链接[等]。"""
    from app.utils.task_tracker import format_retry_task_name
    group_info = group_info or {}
    items = items or []
    magnets = [it.get("magnet", "") for it in items if it.get("magnet")]
    extracted = group_info.get("folder_name") or (items[0].get("resource_name") if len(items) == 1 else None)
    return format_retry_task_name(
        extracted,
        magnets,
        created_at=group_info.get("created_at"),
        link_count=len(magnets) or int(group_info.get("total_count") or 1),
    )


def summarize_group_items(items: List[Dict]) -> Dict:
    """汇总任务组各状态数量、当前尝试次数、下次检查时间与本轮等待时长。"""
    items = items or []
    success = sum(1 for it in items if it.get("status") == "success")
    failed = sum(1 for it in items if it.get("status") == "failed")
    dead = sum(1 for it in items if it.get("status") == "dead")
    pending = sum(1 for it in items if it.get("status") == "pending")
    unfinished = [it for it in items if it.get("status") in ("failed", "dead", "pending")]
    basis = unfinished or items
    attempt = max((int(it.get("retry_count") or 0) for it in basis), default=0) + 1
    failed_items = [it for it in items if it.get("status") == "failed"]
    next_retry_at = min((int(it["next_retry_at"]) for it in failed_items if it.get("next_retry_at")), default=None)
    current_wait = max((int(it["current_wait"]) for it in failed_items if it.get("current_wait")), default=None)
    if failed:
        category = "retry"
    elif pending:
        category = "run"
    elif dead:
        category = "fail"
    else:
        category = "done"
    return {
        "success": success,
        "failed": failed,
        "dead": dead,
        "pending": pending,
        "attempt": attempt,
        "next_retry_at": next_retry_at,
        "current_wait": current_wait,
        "category": category,
    }


def create_single_retry_group(
    title: str,
    magnet: str,
    save_path: str,
    chat_id: Optional[int] = None,
    origin_message_id: Optional[int] = None,
    created_at: Optional[int] = None,
) -> Optional[str]:
    """把单条失败链接作为「1 条链接的任务组」加入统一任务队列，由后台按 2 倍退避自动重试。"""
    if not magnet:
        return None
    from app.utils.bot_settings import BASE_RETRY_WAIT_SECONDS
    from app.utils.task_tracker import is_valid_extracted_filename, is_fallback_retry_name

    with SqlLiteLib() as sqlite:
        rows = sqlite.query_all(
            """
            SELECT bti.group_id FROM batch_task_item bti
            INNER JOIN batch_task_group btg ON btg.group_id = bti.group_id
            WHERE bti.magnet = ? AND btg.save_path = ? AND bti.status IN ('failed', 'pending')
            LIMIT 1
            """,
            (magnet, save_path),
        ) or []
    if rows:
        return rows[0]["group_id"]

    if chat_id is None:
        try:
            chat_id = init.get_primary_user()
        except Exception:
            chat_id = None

    group_id = create_batch_task_group("", save_path, 1, chat_id=chat_id, origin_message_id=origin_message_id, created_at=created_at)
    resource_name = title if (is_valid_extracted_filename(title) and not is_fallback_retry_name(title)) else ""
    now_ts = int(time.time())
    with SqlLiteLib() as sqlite:
        sqlite.execute_sql(
            """
            INSERT INTO batch_task_item
            (group_id, magnet, resource_name, status, retry_count, current_wait, next_retry_at, created_at)
            VALUES (?, ?, ?, 'failed', 0, ?, ?, ?)
            """,
            (group_id, magnet, resource_name, BASE_RETRY_WAIT_SECONDS, now_ts, int(created_at or now_ts)),
        )
    update_task_group_counts(group_id)
    init.logger.info(f"[{title or magnet[:60]}] 已加入统一任务队列等待自动重试（group={group_id}）")
    return group_id


def _parse_sqlite_utc_ts(value) -> Optional[int]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    from datetime import timezone as _tz
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(raw[:19], fmt).replace(tzinfo=_tz.utc).timestamp())
        except ValueError:
            continue
    return None


def migrate_legacy_offline_tasks() -> int:
    """把旧版 offline_task（/rl 列表）中未完成的记录迁移为统一任务组，迁移后标记 is_download=2。"""
    with SqlLiteLib() as sqlite:
        exists = sqlite.query_one(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'offline_task'"
        )
        if not exists:
            return 0
        rows = sqlite.query_all("SELECT * FROM offline_task WHERE is_download = 0 ORDER BY id ASC") or []

    migrated = 0
    for row in rows:
        group_id = create_single_retry_group(
            row.get("title") or "",
            row.get("magnet") or "",
            row.get("save_path") or "",
            created_at=_parse_sqlite_utc_ts(row.get("created_at")),
        )
        with SqlLiteLib() as sqlite:
            sqlite.execute_sql("UPDATE offline_task SET is_download = 2 WHERE id = ?", (row.get("id"),))
        if group_id:
            migrated += 1
    return migrated
