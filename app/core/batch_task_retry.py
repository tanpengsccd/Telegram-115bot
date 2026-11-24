# -*- coding: utf-8 -*-

"""
批量任务重试模块
负责自动检查并重试失败的批量任务
"""

import init
import time
from datetime import datetime
from typing import List, Dict
from app.utils.message_queue import add_task_to_queue
from app.core.batch_task_manager import (
    get_pending_retry_tasks,
    update_retry_attempt,
    mark_task_as_dead,
    update_task_item_status,
    update_task_group_counts,
    get_batch_task_group_info,
    get_dead_task_groups
)


def execute_batch_retry():
    """
    执行批量任务重试
    每10分钟由调度器调用一次
    """
    init.logger.info("🔄 开始检查批量任务重试列表...")

    # 获取待重试任务
    retry_tasks = get_pending_retry_tasks()

    if not retry_tasks:
        init.logger.info("✨ 当前没有待重试的批量任务")
        return

    init.logger.info(f"📋 找到 {len(retry_tasks)} 个待重试任务")

    # 按任务组分组
    grouped_tasks = {}
    for task in retry_tasks:
        group_id = task['group_id']
        if group_id not in grouped_tasks:
            grouped_tasks[group_id] = []
        grouped_tasks[group_id].append(task)

    # 逐组处理重试
    for group_id, tasks in grouped_tasks.items():
        init.logger.info(f"📦 处理任务组 {group_id}，包含 {len(tasks)} 个待重试任务")

        # 获取任务组信息
        group_info = get_batch_task_group_info(group_id)
        if not group_info:
            init.logger.error(f"❌ 任务组 {group_id} 信息不存在")
            continue

        save_path = group_info['save_path']
        folder_name = group_info['folder_name']
        actual_path = f"{save_path}/{folder_name}"

        # 提交重试任务到115
        success_submitted = retry_submit_to_115(tasks, actual_path)

        if not success_submitted:
            init.logger.warn(f"⚠️ 任务组 {group_id} 提交115失败，将在下次重试")
            continue

        # 等待一段时间让任务开始处理
        init.logger.info(f"⏳ 等待60秒后检查重试结果...")
        time.sleep(60)

        # 检查重试结果
        check_retry_results(tasks, actual_path)

        # 更新任务组统计
        update_task_group_counts(group_id)

        # 发送重试结果总结消息给用户
        send_retry_summary(group_id, folder_name)

    init.logger.info("✅ 批量任务重试检查完成")


def retry_submit_to_115(tasks: List[Dict], save_path: str) -> bool:
    """
    将重试任务提交到115离线下载

    Args:
        tasks: 任务列表
        save_path: 保存路径

    Returns:
        是否成功提交
    """
    try:
        # 检查配额
        quota_info = init.openapi_115.get_quota_info()
        left_offline_quota = quota_info['count'] - quota_info['used']

        if left_offline_quota < len(tasks):
            init.logger.warn(f"⚠️ 离线配额不足: 剩余 {left_offline_quota}，需要 {len(tasks)}")
            # 发送通知
            add_task_to_queue(
                init.get_primary_user(),
                f"{init.IMAGE_PATH}/male023.png",
                f"⚠️ 批量任务重试失败：离线配额不足\n剩余配额: {left_offline_quota}\n需要配额: {len(tasks)}"
            )
            return False

        # 构建离线任务URL列表
        offline_urls = "\n".join([task['magnet'] for task in tasks])

        # 提交到115
        offline_success = init.openapi_115.offline_download_specify_path(
            offline_urls,
            save_path
        )

        if offline_success:
            init.logger.info(f"✅ 成功提交 {len(tasks)} 个重试任务到115")
            return True
        else:
            init.logger.error(f"❌ 提交重试任务到115失败")
            return False

    except Exception as e:
        init.logger.error(f"❌ 提交重试任务异常: {e}")
        return False


def check_retry_results(tasks: List[Dict], save_path: str):
    """
    检查重试任务的执行结果

    Args:
        tasks: 任务列表
        save_path: 保存路径
    """
    try:
        # 获取115离线任务状态
        offline_task_status = init.openapi_115.get_offline_tasks()

        if not offline_task_status:
            init.logger.warn("⚠️ 无法获取离线任务状态")
            return

        # 获取下载目录的文件列表
        files_in_dir = set()
        try:
            parent_info = init.openapi_115.get_file_info(save_path)
            if parent_info:
                params = {
                    "cid": parent_info['file_id'],
                    "limit": 1000,
                    "show_dir": 1
                }
                file_list = init.openapi_115.get_file_list(params)
                if file_list:
                    files_in_dir = {f.get('fn', '') for f in file_list}
        except Exception as e:
            init.logger.warn(f"获取目录文件列表失败: {e}")

        # 检查每个任务的状态
        for task in tasks:
            task_id = task['id']
            magnet = task['magnet']
            group_id = task['group_id']
            retry_count = task['retry_count']

            # 在离线任务列表中查找
            task_found = False
            download_success = False
            resource_name = ""

            for offline_task in offline_task_status:
                if offline_task.get('url') == magnet:
                    task_found = True
                    resource_name = offline_task.get('name', '')
                    task_status = offline_task.get('status')
                    task_percent = offline_task.get('percentDone')

                    # 判断是否成功
                    if task_percent == 100 or task_status == 2:
                        download_success = True
                        init.logger.info(f"✅ 任务重试成功: {resource_name}")
                    break

            # 如果不在列表中，检查目录中是否有对应文件
            if not task_found and files_in_dir:
                # 尝试从magnet提取文件名
                if magnet.startswith('ed2k://'):
                    parts = magnet.split('|')
                    if len(parts) >= 3:
                        expected_name = parts[2]
                        # 检查是否存在匹配的文件
                        for filename in files_in_dir:
                            if expected_name in filename or filename in expected_name:
                                download_success = True
                                resource_name = filename
                                init.logger.info(f"✅ 任务重试成功（目录匹配）: {resource_name}")
                                break

            # 更新任务状态
            if download_success:
                # 标记为成功
                update_task_item_status(magnet, group_id, 'success', resource_name)

                # 清理离线任务（如果还在列表中）
                if task_found:
                    for offline_task in offline_task_status:
                        if offline_task.get('url') == magnet:
                            info_hash = offline_task.get('info_hash', '')
                            if info_hash:
                                init.openapi_115.del_faild_offline_task(info_hash)
                            break
            else:
                # 失败，更新重试次数
                new_retry_count = retry_count + 1

                if new_retry_count >= 5:
                    # 达到最大重试次数，标记为死任务
                    mark_task_as_dead(task_id)
                    init.logger.warn(f"☠️ 任务达到最大重试次数，标记为死任务: {magnet[:50]}...")

                    # 发送通知
                    group_info = get_batch_task_group_info(group_id)
                    if group_info:
                        add_task_to_queue(
                            init.get_primary_user(),
                            f"{init.IMAGE_PATH}/male023.png",
                            f"☠️ 批量任务重试失败（已达5次）\n"
                            f"任务组: {group_info['folder_name']}\n"
                            f"磁力: {magnet[:100]}...\n"
                            f"建议手动检查或更换资源"
                        )
                else:
                    # 更新重试信息
                    update_retry_attempt(task_id, new_retry_count)
                    init.logger.info(f"🔄 任务将在稍后进行第 {new_retry_count + 1} 次重试")

                # 清理失败的离线任务
                if task_found:
                    for offline_task in offline_task_status:
                        if offline_task.get('url') == magnet:
                            info_hash = offline_task.get('info_hash', '')
                            if info_hash:
                                init.openapi_115.del_faild_offline_task(info_hash)
                            break

    except Exception as e:
        init.logger.error(f"❌ 检查重试结果异常: {e}")


def send_retry_summary(group_id: str, folder_name: str):
    """
    发送重试任务的总结消息给用户

    Args:
        group_id: 任务组ID
        folder_name: 文件夹名
    """
    try:
        # 获取任务组信息
        group_info = get_batch_task_group_info(group_id)
        if not group_info:
            init.logger.warn(f"任务组 {group_id} 信息不存在，无法发送总结")
            return

        total_count = group_info['total_count']
        success_count = group_info['success_count']
        failed_count = group_info['failed_count']
        created_at = group_info.get('created_at')

        # 只有失败任务数 > 5 才发送详细总结
        if failed_count <= 5:
            init.logger.info(f"任务组 {group_id} 失败任务数 {failed_count} <= 5，不发送总结")
            return

        # 获取所有失败任务的详细信息
        from app.utils.sqlitelib import SqlLiteLib
        with SqlLiteLib() as sqlite:
            sql = """
            SELECT magnet, retry_count FROM batch_task_item
            WHERE group_id = ? AND status = 'failed'
            ORDER BY id ASC
            """
            failed_tasks = sqlite.query_all(sql, (group_id,))

        if not failed_tasks:
            init.logger.info(f"任务组 {group_id} 没有失败任务")
            return

        # 获取当前重试次数（取第一个失败任务的 retry_count）
        current_retry_count = failed_tasks[0].get('retry_count', 0) if failed_tasks else 0

        # 生成错误日志TXT文件
        from datetime import datetime
        import os
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{folder_name}_{timestamp}_retry_{current_retry_count}_errors.txt"

        # 按错误类型分组
        lines = []
        lines.append(f"错误: 离线下载超时（第 {current_retry_count} 次重试后仍失败）")
        lines.append(f"状态: 已自动添加到重试列表")
        for task in failed_tasks:
            lines.append(task['magnet'])
        lines.append("")

        # 写入临时文件
        file_path = f"/tmp/{filename}"
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        # 生成总结消息
        from datetime import datetime
        import time
        current_timestamp = int(time.time())

        # 计算任务持续时间
        if created_at:
            duration_seconds = current_timestamp - created_at
        else:
            duration_seconds = 0

        # 格式化持续时间
        def format_duration(seconds):
            if seconds < 60:
                return f"{seconds}秒"
            elif seconds < 3600:
                minutes = seconds // 60
                return f"{minutes}分钟"
            elif seconds < 86400:
                hours = seconds // 3600
                return f"{hours}小时"
            else:
                days = seconds // 86400
                return f"{days}天"

        duration_str = format_duration(duration_seconds)

        # 格式化首次添加时间
        if created_at:
            created_time = datetime.fromtimestamp(created_at)
            created_time_str = created_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_time_str = "未知"

        # 计算下次检查时间
        from app.core.batch_task_manager import calculate_next_retry_time
        next_retry_timestamp = calculate_next_retry_time(current_retry_count)
        next_check_seconds = next_retry_timestamp - current_timestamp

        # 格式化下次检查时间
        next_check_str = format_duration(next_check_seconds)

        # 计算进行中的任务数
        pending_count = total_count - success_count - failed_count

        # 构建消息
        caption_lines = [
            f"📄 {folder_name} 的批量下载任务",
            f"首次添加: {created_time_str}",
            f"距首次添加已过: {duration_str}",
            f"此为第 {current_retry_count + 1} 次尝试",
            f"此次批量离线任务持续时间: {duration_str}",
            "",
            f"此次下载任务数量统计：",
            f"败: {failed_count} / 续: {pending_count} / 完: {success_count}",
            f"总 {total_count}",
            "",
        ]

        # 如果还会继续重试
        if current_retry_count < 4:  # max_retry = 5
            caption_lines.append(f"现在将重新添加失败的任务")
            caption_lines.append(f"下次检查时间: {next_check_str}后")
            caption_lines.append(f"你也可[删除]该离线任务，无操作将继续尝试")
        else:
            caption_lines.append(f"⚠️ 已达到最大重试次数（5次）")
            caption_lines.append(f"失败的任务将被标记为死任务")
            caption_lines.append(f"请手动检查磁力链接或更换资源")

        caption = "\n".join(caption_lines)

        # 发送文件和消息
        import asyncio
        from telegram import Bot
        bot = Bot(token=init.get_bot_token())

        import nest_asyncio
        nest_asyncio.apply()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        with open(file_path, 'rb') as f:
            loop.run_until_complete(
                bot.send_document(
                    chat_id=init.get_primary_user(),
                    document=f,
                    caption=caption
                )
            )
        loop.close()

        # 删除临时文件
        os.remove(file_path)
        init.logger.info(f"✅ 已发送重试总结消息（第 {current_retry_count + 1} 次尝试）")

    except Exception as e:
        init.logger.error(f"❌ 发送重试总结消息失败: {e}")


def notify_dead_task_groups():
    """
    通知用户包含死任务的任务组
    """
    try:
        dead_groups = get_dead_task_groups()

        if not dead_groups:
            return

        init.logger.info(f"⚠️ 发现 {len(dead_groups)} 个包含死任务的任务组")

        for group in dead_groups:
            group_id = group['group_id']
            folder_name = group['folder_name']
            total_count = group['total_count']
            success_count = group['success_count']
            failed_count = group['failed_count']

            message = f"""
☠️ **批量任务组包含死任务**

**任务组ID:** `{group_id}`
**文件夹:** `{folder_name}`
**完成情况:** {success_count}/{total_count} 成功，{failed_count} 失败

该任务组已尝试重试5次仍有任务失败，建议：
1. 检查磁力链接是否有效
2. 尝试更换资源
3. 手动下载失败的文件
"""

            add_task_to_queue(
                init.get_primary_user(),
                f"{init.IMAGE_PATH}/male023.png",
                message
            )

    except Exception as e:
        init.logger.error(f"❌ 通知死任务异常: {e}")


if __name__ == '__main__':
    # 用于测试
    init.load_yaml_config()
    init.create_logger()
    execute_batch_retry()
