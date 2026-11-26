# -*- coding: utf-8 -*-

import init
from app.utils.sqlitelib import *
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from app.utils.message_queue import add_task_to_queue
from app.core.open_115 import build_offline_task_index, lookup_offline_task
import time
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
from telegram.error import TelegramError
from app.utils.cover_capture import get_movie_cover
from app.utils.task_tracker import (
    format_retry_task_name,
    is_valid_extracted_filename,
    is_fallback_retry_name,
)

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


def get_failed_tasks():
    """获取所有失败的下载任务"""
    with SqlLiteLib() as sqlite:
        sql = "SELECT * FROM offline_task WHERE is_download = 0 ORDER BY id DESC"
        return sqlite.query_all(sql)


def get_failed_task_by_id(task_id: int):
    """按 ID 获取单条手动重试任务"""
    with SqlLiteLib() as sqlite:
        rows = sqlite.query_all("SELECT * FROM offline_task WHERE id = ?", (task_id,))
        return rows[0] if rows else None


def delete_failed_task_by_id(task_id: int) -> bool:
    """按 ID 删除单条手动重试任务"""
    with SqlLiteLib() as sqlite:
        sqlite.execute_sql("DELETE FROM offline_task WHERE id = ?", (task_id,))
    return True


def retry_single_offline_task_now(task_id: int):
    """立即重试单条 /rl 列表中的离线任务"""
    task = get_failed_task_by_id(task_id)
    if not task or task.get("is_download") == 1:
        return {"ok": False, "completed": False, "message": "未找到该待重试任务（可能已完成或已移除）"}

    link = task.get("magnet") or ""
    save_path = task.get("save_path") or init.bot_config.get("offline_path", "/")
    title = task.get("title") or ""
    task_created_at = task.get("created_at")

    if not link or init.openapi_115 is None:
        return {"ok": False, "completed": False, "message": "链接无效或 115 未初始化"}

    try:
        submitted = init.openapi_115.offline_download_specify_path(link, save_path)
    except Exception as e:
        return {"ok": False, "completed": False, "message": f"提交 115 失败：{e}"}

    if not submitted:
        return {"ok": False, "completed": False, "message": "提交 115 离线任务失败"}

    time.sleep(8)
    cloud_list = init.openapi_115.get_offline_tasks_for_links([link], use_cache=False) or []
    matched = lookup_offline_task(build_offline_task_index(cloud_list), link)
    if matched and matched.get("status") in (0, 1) and (matched.get("percentDone") or 0) < 100:
        time.sleep(20)
        cloud_list = init.openapi_115.get_offline_tasks_for_links([link], use_cache=False) or []
        matched = lookup_offline_task(build_offline_task_index(cloud_list), link)

    if matched and (matched.get("status") == 2 or matched.get("percentDone") == 100):
        resource_name = matched.get("name") or ""
        info_hash = matched.get("info_hash") or ""
        mark_task_as_completed(task_id)
        if info_hash:
            try:
                init.openapi_115.del_faild_offline_task(info_hash, delete_source_file=False)
            except Exception:
                pass
        display_name = resource_name or format_retry_task_name(title, link, created_at=task_created_at, link_count=1)
        return {"ok": True, "completed": True, "message": f"✅ 重试成功：{display_name}"}

    update_retry_time(task_id)
    if matched and matched.get("info_hash"):
        try:
            init.openapi_115.del_faild_offline_task(matched["info_hash"], delete_source_file=True)
        except Exception:
            pass
    pct = (matched or {}).get("percentDone", 0)
    return {"ok": True, "completed": False, "message": f"⏳ 本次重试尚未完成（进度 {pct}%），已保留在重试列表中"}

def mark_task_as_completed(task_id: int):
    """标记任务为已完成"""
    with SqlLiteLib() as sqlite:
        sql = "UPDATE offline_task SET is_download = 1, completed_at = datetime('now') WHERE id = ?"
        sqlite.execute_sql(sql, (task_id,))
        
def update_retry_time(task_id: int):
    """更新重试次数"""
    with SqlLiteLib() as sqlite:
        sql = "UPDATE offline_task SET retry_count = retry_count + 1 WHERE id = ?"
        sqlite.execute_sql(sql, (task_id,))
        
def clear_failed_tasks():
    """清空所有失败的重试任务"""
    with SqlLiteLib() as sqlite:
        sql = "DELETE FROM offline_task WHERE is_download = 0"
        sqlite.execute_sql(sql, ())
    

def create_offline_url(res_list):
    """将待重试的离线任务按每批 100 条拼接为换行 URL 列表"""
    offline_tasks = ""
    offline_tasks_list = []
    index = 0
    quota_info = init.openapi_115.get_quota_info()
    if not isinstance(quota_info, dict) or 'count' not in quota_info or 'used' not in quota_info:
        init.logger.warn("获取115离线配额失败，跳过本次重试")
        return None
    left_offline_quota = quota_info['count'] - quota_info['used']
    if left_offline_quota < len(res_list):
        return None
    for item in res_list:
        if not item['magnet']:
            init.logger.warn(f"跳过无效的离线任务，标题: {item['title']}，下载链接为空")
            continue
        offline_tasks += item['magnet'] + "\n"
        index += 1
        if index == 100:
            offline_tasks_list.append(offline_tasks[:-1])
            offline_tasks = ""
            index = 0
    if offline_tasks:
        offline_tasks_list.append(offline_tasks[:-1])
    return offline_tasks_list


def try_to_offline2115_again():
    """重新尝试失败的下载任务"""
    failed_tasks = get_failed_tasks()
    if not failed_tasks:
        init.logger.info("重试列表为空，暂时没有需要重试的任务！")
        return
    
    create_offline_url_list = create_offline_url(failed_tasks)
    if not create_offline_url_list:
        init.logger.warn("离线配额不足或无法获取配额，跳过本次失败任务重试")
        return
    for offline_tasks in create_offline_url_list:
        if not offline_tasks:
            continue
        offline_success = init.openapi_115.offline_download_specify_path(offline_tasks, failed_tasks[0]['save_path'])
        if offline_success:
            init.logger.info(f"重试任务 {offline_tasks} 添加离线成功")
        else:
            init.logger.error(f"重试任务 {offline_tasks} 添加离线失败")
        time.sleep(2)  

    time.sleep(300)  # 等待5秒，确保任务状态更新
    
    offline_task_status = init.openapi_115.get_offline_tasks()
    cloud_index = build_offline_task_index(offline_task_status)

    for task in failed_tasks:
        task_id = task['id']
        link = task['magnet']
        title = task['title']
        save_path = task['save_path']
        retry_count = task['retry_count']
        task_created_at = task.get('created_at')
        cloud_task = lookup_offline_task(cloud_index, link)
        if cloud_task is not None:
            for cloud_task in [cloud_task]:
                if cloud_task['status'] == 2 and cloud_task['percentDone'] == 100:
                    resource_name = cloud_task['name']
                    init.logger.info(f"重试任务 {title} 下载完成！")
                    # 处理下载成功后的清理和重命名准备
                    if init.openapi_115.is_directory(f"{save_path}/{resource_name}"):
                        # 清除垃圾文件
                        init.openapi_115.auto_clean(f"{save_path}/{resource_name}")
                        old_name = f"{save_path}/{resource_name}"
                    else:
                        init.openapi_115.create_dir_for_file(f"{save_path}", "temp")
                        # 移动文件到临时目录
                        init.openapi_115.move_file(f"{save_path}", f"{save_path}/temp")
                        old_name = f"{save_path}/temp"
                    
                    # 若 title 为兜底磁力名或无效占位名，下载成功后优先保留 115 提取到的真实资源名
                    display_title = format_retry_task_name(
                        title,
                        link,
                        created_at=task_created_at,
                        link_count=1,
                    )
                    effective_rename_title = (
                        title
                        if (is_valid_extracted_filename(title) and not is_fallback_retry_name(title))
                        else (resource_name or display_title)
                    )
                    # 执行重命名（过滤 115 非法路径字符）
                    safe_rename_title = effective_rename_title
                    for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
                        safe_rename_title = safe_rename_title.replace(ch, '_')
                    if old_name != f"{save_path}/{safe_rename_title}":
                        init.openapi_115.rename(old_name, safe_rename_title)
                    new_final_path = f"{save_path}/{safe_rename_title}"
                    title = resource_name or display_title
                    file_list = init.openapi_115.get_files_from_dir(new_final_path)
                    # 创建软链
                    from app.handlers.download_handler import create_strm_file, notice_emby_scan_library
                    create_strm_file(new_final_path, file_list)
                    
                    # 发送封面图片（如果有的话）
                    cover_url = ""
                    
                    # 根据分类获取封面
                    cover_url = get_movie_cover(title)
                    
                    # 通知Emby扫库
                    notice_emby_scan_library()
                    # 避免link过长
                    if len(link) > 600:
                        link = link[:600] + "..."
                    
                    message = f"""✅ **重试任务 `{title}` 下载成功！**

**资源名称:** `{title}`
**磁力链接:** `{link}`
**保存路径:** `{save_path}`
        """
                    if cover_url:
                        try:
                            init.logger.info(f"cover_url: {cover_url}")
                            # 发送通知给授权用户
                            add_task_to_queue(
                                init.get_primary_user(),
                                cover_url,
                                message=message
                            )
                        except TelegramError as e:
                            init.logger.warn(f"Telegram API error: {e}")
                        except Exception as e:
                            init.logger.warn(f"Unexpected error: {e}")
                    else:
                        add_task_to_queue(init.get_primary_user(), None, message=message)
                    
                    # 标记任务为完成
                    mark_task_as_completed(task_id)
                else:
                    init.logger.warn(f"重试任务 {title} 下载超时！")
                    # 更新重试次数
                    update_retry_time(task_id)
                    # 删除失败资源
                    init.openapi_115.clear_failed_task(link)
                break
    
    # ⚠️ 注意：clear_cloud_task() 会清空所有离线任务（包括其他用户/批量任务）
    # 建议：应该只清理当前重试的任务（通过 del_faild_offline_task + info_hash）
    # TODO: 改为逐个清理重试任务，避免影响其他任务
    init.openapi_115.clear_cloud_task()

async def view_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rl：重试列表已合并到统一任务队列，直接打开「待重试」筛选"""
    import asyncio
    from app.utils.reaction import REACTION_DONE, set_message_reaction_safe
    from app.handlers.menu_handler import build_tasks_view, send_panel_result, refresh_bottom_panel_if_exists

    if not update.effective_user or not init.check_user(update.effective_user.id):
        if update.message:
            await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return
    chat_id = update.effective_chat.id
    text, markup = await asyncio.to_thread(build_tasks_view, "retry")
    await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)
    
    
async def handle_clear_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理清空重试列表的回调"""
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    
    if callback_data == "clear_all":
        clear_failed_tasks()
        await query.edit_message_text("✅ 重试列表已清空！")
    elif callback_data == "return":
        await query.edit_message_text("操作已取消")


def register_offline_task_handlers(application):
    """注册离线任务处理器"""
    # 添加独立的命令处理器用于查看重试列表
    application.add_handler(CommandHandler("rl", view_retry_list))
    
    # 添加独立的清空重试列表处理器
    application.add_handler(CallbackQueryHandler(handle_clear_retry_list, pattern="^(clear_all|return)$"))
    init.logger.info("✅ Offline Task处理器已注册")