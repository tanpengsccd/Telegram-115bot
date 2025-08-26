# -*- coding: utf-8 -*-

import init
from sqlitelib import *
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from message_queue import add_task_to_queue
import time
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
import base64
import json
from telegram.error import TelegramError
from cover_capture import get_movie_cover

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# 会话状态
RETRY_SPECIFY_NAME = range(70, 71)


def get_failed_tasks():
    """获取所有失败的下载任务"""
    with SqlLiteLib() as sqlite:
        sql = "SELECT * FROM offline_task WHERE is_download = 0"
        return sqlite.query_all(sql)

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
    

def try_to_offline2115_again():
    """重新尝试失败的下载任务"""
    failed_tasks = get_failed_tasks()
    if not failed_tasks:
        init.logger.info("没有需要重试的任务")
        return
    # 清除云端任务，避免重复下载
    init.openapi_115.clear_cloud_task()
    offline_tasks = ""
    for task in failed_tasks:
        task_id = task['id']
        link = task['magnet']
        save_path = task['save_path']
        
        init.logger.info(f"重新尝试下载: {link}")
        offline_tasks += link + "\n"

    offline_tasks = offline_tasks[:-1]  # 去掉最后的换行符
    # 重新尝试下载
    offline_success = init.openapi_115.offline_download_specify_path(offline_tasks, save_path)
    if offline_success:
        init.logger.info(f"重试任务 {task_id} 添加离线成功")
    else:
        init.logger.error(f"重试任务 {task_id} 添加离线失败")

    time.sleep(300)  # 等待5秒，确保任务状态更新
    
    for task in failed_tasks:
        task_id = task['id']
        link = task['magnet']
        save_path = task['save_path']
        retry_count = task['retry_count']
        download_success, resource_name = init.openapi_115.check_offline_download_success_no_waite(link)
        if download_success:
            init.logger.info(f"任务 {task_id} 下载完成: {resource_name}")
            
            # 向用户发送成功通知和重命名请求
            send_retry_success_notification(task_id, link, save_path, resource_name, retry_count)
        else:
            init.logger.warn(f"任务 {task_id} 下载超时")
            # 更新重试次数
            update_retry_time(task_id)
            # 删除失败资源
            init.openapi_115.clear_failed_task(link)
        
        

def send_retry_success_notification(task_id: int, link: str, save_path: str, resource_name: str, retry_count: int):
    """发送重试成功通知并等待用户重命名"""
    
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
    
    # 创建一个简化的键盘，只有重命名选项
    # 将必要信息编码到callback_data中，避免使用临时表
    retry_data = {
        "task_id": task_id,
        "old_name": old_name,
        "save_path": save_path,
        "resource_name": resource_name,
        "link": link,
        "retry_count": retry_count
    }
    encoded_data = base64.b64encode(json.dumps(retry_data).encode()).decode()
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [[InlineKeyboardButton("重命名资源", callback_data=f"retry_rename_{encoded_data}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = f"""✅ **离线重试成功！**

**链接:** `{link[:60]}...`
**保存路径:** `{save_path}`
**原始名称:** `{resource_name}`

点击下方按钮开始重命名："""
    
    # 发送通知给授权用户
    add_task_to_queue(
        init.bot_config['allowed_user'], 
        None, 
        message=message,
        keyboard=reply_markup
    )



async def handle_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理重试任务的回调"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    if callback_data.startswith("retry_rename_"):
        encoded_data = callback_data.replace("retry_rename_", "")
        
        try:
            # 解码数据
            retry_data = json.loads(base64.b64decode(encoded_data).decode())
            
            task_id = retry_data["task_id"]
            old_name = retry_data["old_name"]
            save_path = retry_data["save_path"]
            resource_name = retry_data["resource_name"]
            link = retry_data["link"]
            retry_count = retry_data["retry_count"]
            
            # 保存到用户数据中
            context.user_data["retry_task_id"] = task_id
            context.user_data["retry_old_name"] = old_name
            context.user_data["retry_save_path"] = save_path
            context.user_data["retry_resource_name"] = resource_name
            context.user_data["retry_link"] = link
            context.user_data["retry_count"] = retry_count
            
            await query.edit_message_text(
                text=f"🈯 请输入新的资源名称（点击下方资源名称可复制）：\n\n**`{resource_name}`**",
                parse_mode='MarkdownV2'
            )
            
            return RETRY_SPECIFY_NAME
            
        except Exception as e:
            init.logger.error(f"解码回调数据失败: {e}")
            await query.edit_message_text(text="❌数据解析失败，请重新尝试")
            return ConversationHandler.END

async def handle_retry_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理重试任务的重命名"""
    new_name = update.message.text.strip()
    task_id = context.user_data.get("retry_task_id")
    old_name = context.user_data.get("retry_old_name")
    save_path = context.user_data.get("retry_save_path")
    link = context.user_data.get("retry_link")
    retry_count = context.user_data.get("retry_count", 1)
    
    if not all([task_id, old_name, save_path]):
        await update.message.reply_text("❌任务数据缺失，请重新开始")
        return ConversationHandler.END
    
    try:
        # 执行重命名
        init.openapi_115.rename(old_name, new_name)
        
        # 完成任务处理
        await complete_retry_task_with_message(update, task_id, new_name, save_path, link, retry_count)

        # 清理用户数据
        for key in ["retry_task_id", "retry_old_name", "retry_save_path", "retry_resource_name", "retry_link"]:
            context.user_data.pop(key, None)
            
        return ConversationHandler.END
        
    except Exception as e:
        init.logger.error(f"重命名失败: {e}")
        await update.message.reply_text(f"❌ 重命名失败: {str(e)}")
        return ConversationHandler.END

async def complete_retry_task_with_message(update, task_id: int, new_name: str, save_path: str, link: str, retry_count: int):
    """完成重试任务（消息版本）"""
    # 创建软链文件
    new_full_path = f"{save_path}/{new_name}"
    file_list = init.openapi_115.get_files_from_dir(new_full_path)
    
    # 创建软链
    from download_handler import create_strm_file
    create_strm_file(new_full_path, file_list)
    
    # 发送削刮图片, 如果有的话...
    cover_url = ""
    title = ""
    cover_url = get_movie_cover(new_name)
    if cover_url:
        try:
            init.logger.info(f"cover_url: {cover_url}")
            if title:
                await update.get_bot().send_photo(chat_id=update.effective_chat.id, photo=cover_url, caption=title)
            else:
                await update.get_bot().send_photo(chat_id=update.effective_chat.id, photo=cover_url, caption=new_name)
        except TelegramError as e:
            init.logger.warn(f"Telegram API error: {e}")
        except Exception as e:
            init.logger.warn(f"Unexpected error: {e}")
            
    # 如果已经订阅过
    from subscribe_movie import is_subscribe, update_subscribe
    if is_subscribe(new_name):
        # 更新订阅信息
        update_subscribe(new_name, cover_url, link)
        init.logger.info(f"订阅影片[{new_name}]已手动下载成功！")
    
    # 通知Emby扫库
    from download_handler import notice_emby_scan_library
    notice_emby_scan_library()
    
    # 标记任务为完成
    mark_task_as_completed(task_id)
    
    await update.message.reply_text(
        text=f"**{new_name}下载完成，重试次数：{retry_count}\n👻已通知Emby扫库，请稍后确认！**",
        parse_mode='MarkdownV2'
    )
    return ConversationHandler.END



async def view_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看重试任务列表"""
    retry_list = get_failed_tasks()
    if not retry_list:
        await update.message.reply_text("🈳当前重试列表为空")
        return
   
    retry_text = "**重试列表：**\n\n"
    for i, task in enumerate(retry_list):
        # 使用magnet字段显示，因为offline_task表中可能没有title字段
        retry_text += f"{i + 1}\\. `{task['title']}`\n"
    
    # 显示重试任务列表
    keyboard = [
        [InlineKeyboardButton("清空所有", callback_data="clear_all")],
        [InlineKeyboardButton("返回", callback_data="return")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(retry_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    
    
async def handle_clear_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理清空重试列表的回调"""
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    
    if callback_data == "clear_all":
        clear_failed_tasks()
        await query.edit_message_text("✅重试列表已清空")
        return ConversationHandler.END
    elif callback_data == "return":
        await query.edit_message_text("操作已取消")
        return ConversationHandler.END

async def quit_retry_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """退出重试会话"""
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await update.message.reply_text("🚪用户退出本次会话")
    return ConversationHandler.END


def register_offline_task_handlers(application):
    """注册离线任务处理器"""
    # 重试任务会话处理器
    retry_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_retry_callback, pattern=r"^retry_rename_")],
        states={
            RETRY_SPECIFY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_retry_rename)],
        },
        fallbacks=[CommandHandler("q", quit_retry_conversation)],
    )
    application.add_handler(retry_handler)
    
    # 添加独立的命令处理器用于查看重试列表
    application.add_handler(CommandHandler("rl", view_retry_list))
    
    # 添加独立的清空重试列表处理器
    application.add_handler(CallbackQueryHandler(handle_clear_retry_list, pattern="^(clear_all|return)$"))