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

        # 批量任务组表（使用整数时间戳）
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
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
        sqlite.execute_sql(create_group_table)

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

        if reset_sql:
            init.logger.info("✅ 批量任务数据库表已重置（使用时间戳格式）")
        else:
            init.logger.info("✅ 批量任务数据库表初始化完成（使用时间戳格式）")


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


def create_batch_task_group(folder_name: str, save_path: str, total_count: int) -> str:
    """
    创建批量任务组

    Args:
        folder_name: 文件夹名称
        save_path: 保存路径
        total_count: 总任务数

    Returns:
        group_id: 任务组唯一标识
    """
    group_id = str(uuid.uuid4())[:8]  # 使用8位UUID作为标识
    current_timestamp = int(time.time())

    with SqlLiteLib() as sqlite:
        sql = """
        INSERT INTO batch_task_group
        (group_id, folder_name, save_path, total_count, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """
        sqlite.execute_sql(sql, (group_id, folder_name, save_path, total_count, current_timestamp, current_timestamp))

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

            # 计算第一次重试时间（10分钟后）- 使用Unix时间戳
            next_retry_timestamp = int(time.time()) + (10 * 60)

            sql = """
            INSERT INTO batch_task_item
            (group_id, magnet, status, retry_count, next_retry_at, created_at)
            VALUES (?, ?, 'pending', 0, ?, ?)
            """
            sqlite.execute_sql(sql, (group_id, magnet, next_retry_timestamp, current_timestamp))

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
            SET status = ?, resource_name = ?, completed_at = ?
            WHERE magnet = ? AND group_id = ?
            """
            sqlite.execute_sql(sql, (status, resource_name, completed_timestamp, magnet, group_id))
        else:
            # 失败任务：更新状态
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
    计算下次重试时间

    重试策略:
    - 第1次: 10分钟后
    - 第2次: 1小时后
    - 第3次: 10小时后
    - 第4次: 1天后
    - 第5次: 10天后

    Args:
        retry_count: 当前重试次数

    Returns:
        下次重试时间（Unix时间戳）
    """
    now_timestamp = int(time.time())

    if retry_count == 0:
        return now_timestamp + (10 * 60)  # 10分钟
    elif retry_count == 1:
        return now_timestamp + (1 * 60 * 60)  # 1小时
    elif retry_count == 2:
        return now_timestamp + (10 * 60 * 60)  # 10小时
    elif retry_count == 3:
        return now_timestamp + (1 * 24 * 60 * 60)  # 1天
    elif retry_count == 4:
        return now_timestamp + (10 * 24 * 60 * 60)  # 10天
    else:
        # 超过5次，不再重试
        return now_timestamp + (365 * 24 * 60 * 60)  # 设置为一年后（实际上会被标记为dead）


def get_pending_retry_tasks() -> List[Dict]:
    """
    获取待重试的任务

    Returns:
        待重试任务列表
    """
    current_timestamp = int(time.time())

    with SqlLiteLib() as sqlite:
        sql = """
        SELECT * FROM batch_task_item
        WHERE status = 'failed'
        AND retry_count < max_retry
        AND next_retry_at <= ?
        ORDER BY next_retry_at ASC
        LIMIT 100
        """
        tasks = sqlite.query_all(sql, (current_timestamp,))
        return tasks if tasks else []


def update_retry_attempt(task_id: int, retry_count: int) -> None:
    """
    更新重试尝试信息

    Args:
        task_id: 任务ID
        retry_count: 新的重试次数
    """
    next_retry_timestamp = calculate_next_retry_time(retry_count)
    current_timestamp = int(time.time())

    with SqlLiteLib() as sqlite:
        sql = """
        UPDATE batch_task_item
        SET retry_count = ?,
            last_retry_at = ?,
            next_retry_at = ?
        WHERE id = ?
        """
        sqlite.execute_sql(sql, (retry_count, current_timestamp, next_retry_timestamp, task_id))


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
        txt_filename = f"{folder_name}.txt"

        # 先写入本地临时文件
        import os
        temp_txt_path = os.path.join(init.TEMP, txt_filename)
        with open(temp_txt_path, 'w', encoding='utf-8') as f:
            f.write(txt_content)

        # 上传到115云盘
        upload_result = init.openapi_115.upload_file(temp_txt_path, folder_path)

        # 删除临时文件
        os.remove(temp_txt_path)

        if upload_result:
            init.logger.info(f"📄 已生成磁力链接TXT文件: {folder_path}/{txt_filename}")
            return True
        else:
            init.logger.error(f"❌ 上传TXT文件失败: {txt_filename}")
            return False

    except Exception as e:
        init.logger.error(f"生成磁力链接TXT文件失败: {e}")
        return False


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
