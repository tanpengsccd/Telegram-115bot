# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError
import init
import os
import re
import time
import asyncio
from pathlib import Path
from app.utils.cover_capture import get_movie_cover, get_av_cover
import requests
from enum import Enum
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
from app.utils.sqlitelib import *
from concurrent.futures import ThreadPoolExecutor
from app.utils.reaction import (
    REACTION_DONE,
    REACTION_ERROR,
    set_message_reaction_safe,
    schedule_reactions_batch,
)
from app.utils.task_tracker import (
    register_task,
    update_task,
    update_task_item,
    finish_task,
    format_retry_task_name,
)

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SELECT_MAIN_CATEGORY, SELECT_SUB_CATEGORY = range(10, 12)

# 全局线程池，用于处理下载任务
download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="Movie_Download")

class DownloadUrlType(Enum):
    ED2K = "ED2K"
    THUNDER = "thunder"
    MAGNET = "magnet"
    UNKNOWN = "unknown"
    
    def __str__(self):
        return self.value


def _ensure_bot_session():
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    return init.bot_session


def _ensure_user_last_save_map():
    session = _ensure_bot_session()
    if 'user_last_save' not in session or not isinstance(session['user_last_save'], dict):
        session['user_last_save'] = {}
    return session['user_last_save']


def remember_user_last_save_path(user_id: int, selected_path: str):
    session = _ensure_bot_session()
    session['movie_last_save'] = selected_path  # 兼容旧逻辑
    user_map = _ensure_user_last_save_map()
    user_map[user_id] = selected_path


def get_user_last_save_path(user_id: int):
    if hasattr(init, 'bot_session'):
        session = init.bot_session
        user_map = session.get('user_last_save')
        if isinstance(user_map, dict):
            return user_map.get(user_id)
    return None


def clear_user_last_save_path(user_id: int = None):
    if not hasattr(init, 'bot_session'):
        return False

    cleared = False
    session = init.bot_session

    if user_id is None:
        if 'movie_last_save' in session:
            session.pop('movie_last_save', None)
            cleared = True
        if 'user_last_save' in session:
            session.pop('user_last_save', None)
            cleared = True
    else:
        user_map = session.get('user_last_save')
        if isinstance(user_map, dict) and user_id in user_map:
            user_map.pop(user_id, None)
            cleared = True
    return cleared


async def finalize_download_submission(context, user_id, selected_path, custom_folder_name, *,
                                       query=None, chat_id=None, auto_note=""):
    links = context.user_data.get("links", [])
    if not links:
        message = "❌ 未找到下载链接数据，请重新发送链接"
        if query:
            await query.edit_message_text(message)
        elif chat_id is not None:
            await context.bot.send_message(chat_id=chat_id, text=message)
        return ConversationHandler.END

    total_count = len(links)
    remember_user_last_save_path(user_id, selected_path)

    message_lines = [
        f"✅ 已为您添加 {total_count} 个下载任务到队列！",
        f"📁 保存路径: {selected_path}",
        "请稍后~"
    ]
    if auto_note:
        message_lines.append(auto_note)
    text = "\n".join(message_lines)

    if query:
        await query.edit_message_text(text)
    elif chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    reaction_targets = list(context.user_data.get("reaction_targets") or [])

    # 使用批量下载处理（减少API调用频率）
    download_executor.submit(
        download_tasks_batch,
        links,
        selected_path,
        user_id,
        custom_folder_name,
        reaction_targets,
    )

    # 清理用户数据
    context.user_data.clear()

    # 若当前对话存在常驻底部面板，将其重新置底
    target_chat_id = chat_id
    if target_chat_id is None and query and getattr(query, "message", None):
        target_chat_id = query.message.chat_id
    if target_chat_id:
        from app.handlers.menu_handler import refresh_bottom_panel_if_exists
        await refresh_bottom_panel_if_exists(context.bot, target_chat_id)

    return ConversationHandler.END


def sanitize_folder_name(text: str) -> str:
    """
    清理文件夹名称:
    1. 移除":"字符
    2. 替换不能作为文件夹的字符为"-"
    """
    if not text:
        return ""

    # 1. 移除冒号
    text = text.replace(":", "")

    # 2. 替换不能作为文件夹名的字符为"-"
    # Windows和Linux都不允许的字符: / \ ? * " < > |
    invalid_chars = ['/', '\\', '?', '*', '"', '<', '>', '|']
    for char in invalid_chars:
        text = text.replace(char, "-")

    # 移除前后空格
    return text.strip()


def extract_folder_name_from_text(message_text: str) -> str:
    """
    从文本中提取首行+尾行非链接文字作为文件夹名

    规则:
    1. 提取首行非空文字(不包含链接)
    2. 提取尾行非空文字(不包含链接)
    3. 拼接首行+尾行
    4. 清理文件夹名称
    """
    lines = message_text.strip().split('\n')

    # 过滤掉空行和只包含链接的行（使用统一的链接检测函数）
    non_link_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 使用统一的链接检测函数（支持带前缀的链接）
        if line_contains_link(line):
            # 该行包含链接，跳过整行
            continue

        # 该行不包含链接，直接使用
        non_link_lines.append(line)

    if not non_link_lines:
        return ""

    # 拼接首行和尾行
    if len(non_link_lines) == 1:
        folder_name = non_link_lines[0]
    else:
        folder_name = non_link_lines[0] + non_link_lines[-1]

    # 清理文件夹名称
    return sanitize_folder_name(folder_name)


async def trigger_batch_download_after_delay(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, delay: float):
    """
    等待指定时间后触发批量下载

    Args:
        user_id: 用户ID
        chat_id: 聊天ID
        context: Telegram上下文
        delay: 延迟时间（秒）
    """
    try:
        await asyncio.sleep(delay)

        # 取出缓冲区数据
        buffer = init.pending_messages_buffer.pop(user_id, None)
        if not buffer:
            return

        # 合并所有消息文本
        merged_text = "\n".join(buffer["messages"])

        # 从合并后的文本提取文件夹名（首行 + 尾行）
        custom_folder_name = extract_folder_name_from_text(merged_text)

        # 保存到 context
        context.user_data["links"] = buffer["links"]
        context.user_data["custom_folder_name"] = custom_folder_name
        context.user_data["total_links"] = len(buffer["links"])
        context.user_data["reaction_targets"] = list(buffer.get("reaction_targets") or [])

        if custom_folder_name:
            init.logger.info(f"从合并消息中提取到自定义文件夹名: {custom_folder_name}")

        # 如果用户曾经选择过保存路径，则直接复用，免去再次选择
        last_save_path = get_user_last_save_path(user_id)
        if last_save_path:
            auto_note = "🤖 已自动使用上次的保存路径，发送 /reset_save_path 可重新选择。"
            await finalize_download_submission(
                context,
                user_id,
                last_save_path,
                custom_folder_name,
                chat_id=chat_id,
                auto_note=auto_note
            )
            return

        # 构建分类选择键盘
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])]
            for category in init.bot_config['category_folder']
        ]
        keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # 发送选择消息
        link_count = len(buffer["links"])

        # 获取配额信息
        quota_info_text = ""
        quota_warning = ""
        try:
            quota_info = init.openapi_115.get_quota_info()
            if quota_info:
                total_quota = quota_info.get('count', 0)
                used_quota = quota_info.get('used', 0)
                remaining_quota = total_quota - used_quota
                quota_info_text = f"\n💾 本月配额还剩 {remaining_quota}/{total_quota} 个。即将消耗 {link_count} 个下载配额。"

                if remaining_quota < link_count:
                    quota_warning = "\n⚠️ 配额不足以完成此次任务，可能无法完成所有下载。"
        except Exception as e:
            init.logger.warn(f"获取配额信息失败: {e}")

        # 提取文件夹名（用于显示）
        folder_hint = ""
        if custom_folder_name:
            folder_hint = f"\n📁 即将下载至: 选择的文件夹/[{custom_folder_name}]"

        message_text = f"📥 检测到 {link_count} 个下载链接{folder_hint}{quota_info_text}{quota_warning}\n\n❓请选择要保存到哪个分类："

        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup
        )

    except asyncio.CancelledError:
        # 定时器被取消（有新消息到达）
        init.logger.debug(f"用户 {user_id} 的定时器被新消息取消")
        raise
    except Exception as e:
        init.logger.error(f"触发批量下载失败: {e}")
        # 清理缓冲区
        init.pending_messages_buffer.pop(user_id, None)


LINK_PROTOCOL_RE = re.compile(r'(magnet:|ed2k://|thunder://)', re.IGNORECASE)


def extract_message_text_and_entity_links(message) -> str:
    """提取消息正文、媒体配文(caption)及 text_link 实体中的链接文本"""
    if message is None:
        return ""
    parts = []
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if text:
        parts.append(text.strip())
    for entities_attr in ("entities", "caption_entities"):
        entities = getattr(message, entities_attr, None) or []
        for ent in entities:
            url = getattr(ent, "url", None)
            if url and LINK_PROTOCOL_RE.search(url) and url not in text:
                parts.append(url)
    return "\n".join(parts).strip()


class DownloadLinkFilter(filters.MessageFilter):
    """匹配正文、媒体配文(caption)或超链接实体中包含磁力/ed2k/迅雷链接的消息"""

    def filter(self, message) -> bool:
        if message is None:
            return False
        # .txt 文档交给 handle_txt_file 处理，避免重复触发
        doc = getattr(message, "document", None)
        if doc and (getattr(doc, "file_name", None) or "").lower().endswith(".txt"):
            return False
        combined = extract_message_text_and_entity_links(message)
        return bool(combined and LINK_PROTOCOL_RE.search(combined))


DOWNLOAD_LINK_FILTER = DownloadLinkFilter()


async def start_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return ConversationHandler.END

    message_text = extract_message_text_and_entity_links(update.message)
    chat_id = update.effective_chat.id

    # 解析多个下载链接（使用统一的链接提取函数）
    lines = message_text.split('\n')
    valid_links = extract_links_from_lines(lines)

    # 检查是否有有效链接
    if not valid_links:
        await update.message.reply_text("⚠️ 未找到有效的下载链接，请检查格式后重试！")
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_ERROR)
        return ConversationHandler.END

    # 自动忽略无关信息，只提示找到的有效链接数量
    init.logger.info(f"用户 {usr_id} 发送消息，包含 {len(valid_links)} 个有效链接")

    # === 消息合并逻辑 ===
    # 获取消息合并间隔（毫秒转秒）
    merge_interval_ms = init.bot_config.get('msgs_merge_interval', 3000)
    merge_interval_sec = merge_interval_ms / 1000.0

    # 获取/创建用户缓冲区
    buffer = init.pending_messages_buffer.setdefault(usr_id, {
        "messages": [],
        "links": [],
        "reaction_targets": [],
        "timer_task": None,
        "context": context,
        "chat_id": chat_id
    })

    # 添加消息文本、链接及待更新表情的目标消息到缓冲区
    buffer["messages"].append(message_text)
    buffer["links"].extend(valid_links)
    if update.message:
        buffer.setdefault("reaction_targets", []).append((chat_id, update.message.message_id))
    buffer["context"] = context  # 更新context
    buffer["chat_id"] = chat_id  # 更新chat_id

    # 取消旧定时器
    if buffer["timer_task"] and not buffer["timer_task"].done():
        buffer["timer_task"].cancel()
        init.logger.debug(f"用户 {usr_id} 有新消息，取消旧定时器")

    # 启动新的定时器
    buffer["timer_task"] = asyncio.create_task(
        trigger_batch_download_after_delay(usr_id, chat_id, context, merge_interval_sec)
    )

    # 发送提示（收集中）
    total_links = len(buffer["links"])
    await update.message.reply_text(
        f"📥 正在收集链接... 已收集 {total_links} 个\n⏳ 等待 {merge_interval_sec:.1f} 秒后处理"
    )

    # 不返回 ConversationHandler 状态，等待定时器触发
    return ConversationHandler.END


async def select_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    query_data = query.data
    if query_data == "cancel":
        return await quit_conversation(update, context)
    elif query_data == "last_save_path":
        user_id = update.effective_user.id
        last_save_path = get_user_last_save_path(user_id)
        if last_save_path:
            custom_folder_name = context.user_data.get("custom_folder_name", "")
            return await finalize_download_submission(
                context,
                user_id,
                last_save_path,
                custom_folder_name,
                query=query
            )
        await query.edit_message_text("❌ 未找到最后一次保存路径，请重新选择分类")
        return ConversationHandler.END
    else:
        context.user_data["selected_main_category"] = query_data
        custom_folder_name = context.user_data.get("custom_folder_name", "")
        user_id = update.effective_user.id
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == query_data
        ][0]

        if len(sub_categories) == 1:
            selected_path = sub_categories[0]['path']
            auto_note = f"📁 {sub_categories[0]['name']} 只有一个保存目录，已自动选择。"
            return await finalize_download_submission(
                context,
                user_id,
                selected_path,
                custom_folder_name,
                query=query,
                auto_note=auto_note
            )

        # 创建子分类按钮
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=category['path'])] for category in sub_categories
        ]
        keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("❓请选择分类保存目录：", reply_markup=reply_markup)

        return SELECT_SUB_CATEGORY


async def select_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 获取用户选择的路径
    selected_path = query.data
    if selected_path == "cancel":
        return await quit_conversation(update, context)
    user_id = update.effective_user.id

    # 获取自定义文件夹名(如果有)
    custom_folder_name = context.user_data.get("custom_folder_name", "")

    return await finalize_download_submission(
        context,
        user_id,
        selected_path,
        custom_folder_name,
        query=query
    )


async def reset_save_path_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cleared = clear_user_last_save_path(user_id)
    if cleared:
        await update.message.reply_text("✅ 已清除保存路径，下次会重新提示分类。")
    else:
        await update.message.reply_text("ℹ️ 当前没有记录的保存路径。")
    if update.message:
        await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_DONE)


# === 独立的全局回调处理器（支持定时器触发的消息）===

async def handle_category_selection_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    全局分类选择处理器（不依赖ConversationHandler状态）
    用于处理定时器触发后发送的消息中的按钮点击
    """
    query = update.callback_query
    await query.answer()

    query_data = query.data
    user_id = update.effective_user.id

    # 检查用户权限
    if not init.check_user(user_id):
        await query.edit_message_text("⚠️ 对不起，您无权使用115机器人！")
        return

    # 获取用户数据
    links = context.user_data.get("links", [])
    if not links:
        await query.edit_message_text("❌ 未找到下载链接数据，请重新发送链接")
        return

    custom_folder_name = context.user_data.get("custom_folder_name", "")

    # 处理取消
    if query_data == "cancel":
        await query.edit_message_text("🚪 已取消下载任务")
        # 清理用户数据
        context.user_data.clear()
        return

    # 处理"上次保存路径"
    if query_data == "last_save_path":
        last_save_path = get_user_last_save_path(user_id)
        if last_save_path:
            await finalize_download_submission(
                context,
                user_id,
                last_save_path,
                custom_folder_name,
                query=query
            )
        else:
            await query.edit_message_text("❌ 未找到最后一次保存路径，请重新选择分类")
        return

    # 检查是否是主分类（movies, av等）
    main_categories = [cat['name'] for cat in init.bot_config.get('category_folder', [])]
    if query_data in main_categories:
        # 保存选择的主分类
        context.user_data["selected_main_category"] = query_data

        # 获取子分类
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"]
            if item['name'] == query_data
        ][0]

        if len(sub_categories) == 1:
            selected_path = sub_categories[0]['path']
            auto_note = f"📁 {sub_categories[0]['name']} 只有一个保存目录，已自动选择。"
            await finalize_download_submission(
                context,
                user_id,
                selected_path,
                custom_folder_name,
                query=query,
                auto_note=auto_note
            )
            return

        # 创建子分类按钮
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=category['path'])]
            for category in sub_categories
        ]
        keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("❓请选择分类保存目录：", reply_markup=reply_markup)
        return

    # 否则视为子分类路径
    selected_path = query_data

    await finalize_download_submission(
        context,
        user_id,
        selected_path,
        custom_folder_name,
        query=query
    )


async def handle_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理重试任务的回调"""
    query = update.callback_query
    await query.answer()
    
    try:
        # 从callback_data中提取task_id
        task_id = query.data.replace("retry_", "")
        
        # 从全局存储中获取任务数据
        if hasattr(init, 'pending_tasks') and task_id in init.pending_tasks:
            task_data = init.pending_tasks[task_id]
            
            # 添加到重试列表
            save_failed_download_to_db(
                task_data["resource_name"], 
                task_data["link"], 
                task_data["selected_path"]
            )
            
            await query.edit_message_text("✅ 已将失败任务添加到重试列表，系统将自动重试！")
            
            # 清理已使用的任务数据
            del init.pending_tasks[task_id]
        else:
            await query.edit_message_text("❌ 任务数据已过期")
        
    except Exception as e:
        init.logger.error(f"处理重试回调失败: {e}")
        await query.edit_message_text("❌ 添加到重试列表失败，请稍后再试")


async def handle_download_failure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理下载失败时的用户选择"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    
    if choice == "cancel_download":
        # 取消下载
        await query.edit_message_text("✅ 已取消，可尝试更换磁力重试！")


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
    return ConversationHandler.END


def is_valid_link(link: str) -> DownloadUrlType:
    # 定义链接模式字典
    patterns = {
        DownloadUrlType.MAGNET: r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$',
        DownloadUrlType.ED2K: r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        DownloadUrlType.THUNDER: r'^thunder://[a-zA-Z0-9=]+'
    }

    # 检查基本链接类型
    for url_type, pattern in patterns.items():
        if re.match(pattern, link):
            return url_type

    return DownloadUrlType.UNKNOWN


def line_contains_link(line: str) -> bool:
    """
    检查一行文本是否包含下载链接（包括带前缀的链接）

    Args:
        line: 文本行

    Returns:
        True 如果包含链接，False 否则
    """
    line = line.strip()
    if not line:
        return False

    # 检查整行是否是有效链接
    if is_valid_link(line) != DownloadUrlType.UNKNOWN:
        return True

    # 定义链接检测的正则表达式
    link_detection_patterns = [
        r'ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?',
        r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}(?:[&\?][^\s]*)?',
        r'thunder://[a-zA-Z0-9=]+',
    ]

    # 使用正则表达式检测行中是否包含链接
    for pattern in link_detection_patterns:
        if re.search(pattern, line, re.IGNORECASE):
            return True

    # 按空格分割检查
    for fragment in line.split():
        if is_valid_link(fragment.strip()) != DownloadUrlType.UNKNOWN:
            return True

    return False


def extract_links_from_lines(lines: list) -> list:
    """
    统一的链接提取函数，从文本行中提取所有有效的下载链接

    支持格式：
    1. 纯链接行：magnet:?xt=urn:btih:...
    2. 带前缀的链接：链接: ed2k://|file|...
    3. 同行多个链接（按空格分割）
    4. 文件名包含空格的 ed2k 链接

    Args:
        lines: 文本行列表

    Returns:
        包含链接信息的列表，格式: [{"link": "...", "type": DownloadUrlType}, ...]
    """
    valid_links = []

    # 定义链接提取的正则表达式（用于从文本中提取链接）
    link_extraction_patterns = [
        (DownloadUrlType.ED2K, r'ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?'),
        (DownloadUrlType.MAGNET, r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}(?:[&\?][^\s]*)?'),
        (DownloadUrlType.THUNDER, r'thunder://[a-zA-Z0-9=]+'),
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检查整行是否是有效链接
        dl_url_type = is_valid_link(line)
        if dl_url_type != DownloadUrlType.UNKNOWN:
            valid_links.append({"link": line, "type": dl_url_type})
            continue

        # 尝试从行中提取链接（支持 "链接: ed2k://..." 这样的格式）
        found_in_line = False
        for link_type, pattern in link_extraction_patterns:
            matches = re.findall(pattern, line, re.IGNORECASE)
            for match in matches:
                # 验证提取的链接是否有效
                if is_valid_link(match) != DownloadUrlType.UNKNOWN:
                    valid_links.append({"link": match, "type": link_type})
                    found_in_line = True

        if found_in_line:
            continue

        # 如果整行不是链接且没有提取到链接，尝试按空格分割（兼容多个链接在同一行的情况）
        fragments = line.split()
        for fragment in fragments:
            fragment = fragment.strip()
            if not fragment:
                continue
            dl_url_type = is_valid_link(fragment)
            if dl_url_type != DownloadUrlType.UNKNOWN:
                valid_links.append({"link": fragment, "type": dl_url_type})

    return valid_links


def create_strm_file(new_name, file_list):
    # 检查是否需要创建软链
    if not init.bot_config['create_strm']:
        return
    try:
        init.logger.debug(f"Original new_name: {new_name}")

        # 获取根目录
        cd2_mount_root = Path(init.bot_config['mount_root'])
        strm_root = Path(init.bot_config['strm_root'])

        # 构建目标路径和 .strm 文件的路径
        relative_path = Path(new_name).relative_to(Path(new_name).anchor)
        cd2_mount_path = cd2_mount_root.joinpath(relative_path)
        strm_path = strm_root.joinpath(relative_path)

        # 日志输出以验证路径
        init.logger.debug(f"cd2_mount_root: {cd2_mount_root}")
        init.logger.debug(f"strm_root: {strm_root}")
        init.logger.debug(f"cd2_mount_path: {cd2_mount_path}")
        init.logger.debug(f"strm_path: {strm_path}")

        # 确保 strm_path 路径存在
        if not strm_path.exists():
            strm_path.mkdir(parents=True, exist_ok=True)

        # 遍历文件列表，创建 .strm 文件
        for file in file_list:
            target_file = strm_path / (Path(file).stem + ".strm")
            mkv_file = cd2_mount_path / file

            # 日志输出以验证 .strm 文件和目标文件
            init.logger.debug(f"target_file (.strm): {target_file}")
            init.logger.debug(f"mkv_file (.mp4): {mkv_file}")

            # 如果原始文件存在，写入 .strm 文件
            # if mkv_file.exists():
            with target_file.open('w', encoding='utf-8') as f:
                f.write(str(mkv_file))
                init.logger.info(f"strm文件创建成功，{target_file} -> {mkv_file}")
            # else:
            #     init.logger.info(f"原始视频文件[{mkv_file}]不存在！")
    except Exception as e:
        init.logger.info(f"Error creating .strm files: {e}")


def notice_emby_scan_library():
    emby_server = init.bot_config['emby_server']
    api_key = init.bot_config['api_key']
    if api_key is None or api_key.strip() == "" or api_key.strip().lower() == "your_api_key":
        init.logger.warn("Emby API Key 未配置，跳过通知Emby扫库")
        return False
    if str(emby_server).endswith("/"):
        emby_server = emby_server[:-1]
    url = f"{emby_server}/Library/Refresh"
    headers = {
        "X-Emby-Token": api_key
    }
    emby_response = requests.post(url, headers=headers)
    if emby_response.text == "":
        init.logger.info("通知Emby扫库成功！")
        return True
    else:
        init.logger.error(f"通知Emby扫库失败：{emby_response}")
        return False


def save_failed_download_to_db(title, magnet, save_path, chat_id=None, origin_message_id=None):
    """保存失败的下载任务：统一加入任务队列（1 条链接的任务组），由后台按 2 倍退避自动重试"""
    from app.core.batch_task_manager import create_single_retry_group
    return create_single_retry_group(
        title or "",
        magnet,
        save_path,
        chat_id=chat_id,
        origin_message_id=origin_message_id,
    )


def format_batch_progress_message(total_count, task_results, max_chars=4096):
    """
    格式化批量下载进度消息，限制字符数并优先显示错误

    Args:
        total_count: 总任务数
        task_results: 任务结果列表 [{"index": 1, "status": "success/failed", "name": "...", "link": "..."}]
        max_chars: 最大字符数（Telegram单条消息限制4096字符）

    Returns:
        格式化的消息文本
    """
    # 分离成功和失败的任务
    failed_tasks = [t for t in task_results if t.get("status") == "failed"]
    success_tasks = [t for t in task_results if t.get("status") == "success"]

    # 构建消息头
    header_lines = [f"正在进行的总 {total_count} 个链接的下载任务："]

    # 如果有失败任务，添加失败提示头
    if failed_tasks:
        header_lines.append("❌ 离线下载超时，已添加到重试列表：")

    header_text = "\n".join(header_lines)

    # 预留字符空间
    # - 头部文本
    # - 省略提示（如果需要）：约80字符 × 2条（失败+成功）
    # - 安全边距：100字符
    reserved_chars = len(header_text) + 80 * 2 + 100
    available_chars = max_chars - reserved_chars

    # 优先显示错误链接
    displayed_failed = []
    displayed_success = []
    omitted_failed_count = 0
    omitted_success_count = 0
    current_length = 0

    # 第一步：优先添加所有失败任务的链接
    for task in failed_tasks:
        # 完整显示失败链接，不截断（方便用户复制）
        display_link = task.get("link", "")
        line_length = len(display_link) + 1  # +1 for newline

        # 检查是否超出可用字符数
        if current_length + line_length > available_chars:
            # 超出限制，剩余的失败任务将被省略
            omitted_failed_count = len(failed_tasks) - len(displayed_failed)
            break

        displayed_failed.append(display_link)
        current_length += line_length

    # 第二步：如果还有空间，添加成功任务（显示最新的）
    remaining_chars = available_chars - current_length

    if remaining_chars > 100:  # 至少有100字符才显示成功任务
        # 倒序遍历最新的成功任务
        for task in reversed(success_tasks):
            line = f"✅ {task['index']}/{total_count} [{task.get('name', '未知')}]"
            line_length = len(line) + 1  # +1 for newline

            # 检查是否超出剩余字符数
            if current_length + line_length > available_chars:
                break

            displayed_success.insert(0, line)  # 插入到开头保持顺序
            current_length += line_length

        omitted_success_count = len(success_tasks) - len(displayed_success)
    else:
        omitted_success_count = len(success_tasks)

    # 第三步：组装最终消息
    message_parts = [header_text]

    # 添加失败任务省略提示
    if omitted_failed_count > 0:
        message_parts.append(f"...此处省略 {omitted_failed_count} 条失败详情，下载完成后将发送完整错误日志TXT文件")

    # 添加失败任务列表
    if displayed_failed:
        message_parts.extend(displayed_failed)

    # 添加成功任务省略提示
    if omitted_success_count > 0 and displayed_success:
        message_parts.append(f"...此处省略 {omitted_success_count} 条成功详情...")

    # 添加成功任务列表
    if displayed_success:
        message_parts.extend(displayed_success)

    final_message = "\n".join(message_parts)

    # 最终安全检查：如果仍然超出4096，强制截断
    if len(final_message) > max_chars:
        truncate_pos = max_chars - 50  # 预留空间添加截断提示
        final_message = final_message[:truncate_pos] + "\n...(消息过长已截断)"

    return final_message


def generate_batch_summary_caption(custom_folder_name, batch_group_id, failed_count, success_count, total_count, created_at):
    """
    生成批量下载完成后的详细总结消息

    Args:
        custom_folder_name: 自定义文件夹名
        batch_group_id: 批量任务组ID
        failed_count: 失败任务数
        success_count: 成功任务数（进度100的任务）
        total_count: 总任务数
        created_at: 任务创建时间（Unix时间戳）

    Returns:
        格式化的消息文本
    """
    from datetime import datetime
    import time

    # 当前时间
    now = datetime.now()
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

    # 格式化首次添加时间（转换为本地时区）
    if created_at:
        # 使用本地时区
        created_time = datetime.fromtimestamp(created_at)
        created_time_str = created_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        created_time_str = "未知"

    # 计算下次检查时间（10分钟后）
    from app.core.batch_task_manager import calculate_next_retry_time
    next_retry_timestamp = calculate_next_retry_time(0)
    next_check_minutes = (next_retry_timestamp - current_timestamp) // 60

    # 计算进行中的任务数（total - success - failed）
    pending_count = total_count - success_count - failed_count

    # 显示文件夹名
    folder_display = custom_folder_name if custom_folder_name else "默认文件夹"

    # 构建消息
    caption_lines = [
        f"📄 {folder_display} 的批量下载任务",
        f"首次添加: {created_time_str}",
        f"距首次添加已过: {duration_str}",
        f"此为第 1 次尝试",
        f"此次批量离线任务持续时间: {duration_str}",
        "",
        f"此次下载任务数量统计：",
        f"败: {failed_count} / 续: {pending_count} / 完: {success_count}",
        f"总 {total_count}",
        "",
        f"现在将重新添加失败的任务",
        f"下次检查时间: {next_check_minutes}分钟后",
        f"你也可[删除]该离线任务，无操作将继续尝试"
    ]

    return "\n".join(caption_lines)


def generate_error_log_file(task_results, total_count, custom_folder_name="", batch_group_id=None):
    """
    生成批量下载错误日志TXT文件

    Args:
        task_results: 任务结果列表
        total_count: 总任务数
        custom_folder_name: 自定义文件夹名
        batch_group_id: 批量任务组ID

    Returns:
        文件路径，如果没有失败任务则返回None
    """
    failed_tasks = [t for t in task_results if t.get("status") == "failed"]

    if not failed_tasks:
        return None

    # 生成文件内容
    from datetime import datetime
    import tempfile

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 使用自定义文件夹名或默认名称
    if custom_folder_name:
        filename = f"{custom_folder_name}_{timestamp}_errors.txt"
    else:
        filename = f"batch_download_errors_{timestamp}.txt"

    # 按错误类型分组
    error_groups = {}
    for task in failed_tasks:
        error_msg = "离线下载超时"  # 当前所有失败都是超时
        if error_msg not in error_groups:
            error_groups[error_msg] = []
        error_groups[error_msg].append(task.get('link', '未知'))

    # 构建简化的文本内容
    lines = []
    for error_type, links in error_groups.items():
        lines.append(f"错误: {error_type}")
        lines.append(f"状态: 已自动添加到重试列表")
        for link in links:
            lines.append(link)
        lines.append("")  # 空行分隔

    # 写入临时文件
    file_path = f"/tmp/{filename}"
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return file_path


def process_successful_download_silent(link, selected_path, user_id, resource_name, task_index, total_count):
    """
    处理下载成功的任务（静默版本，不发送消息）
    用于批量下载时的进度跟踪
    返回最终的资源名称（用于批量移动）
    """
    progress_info = f"[{task_index}/{total_count}]"
    init.logger.info(f"{progress_info} {resource_name} 开始处理下载结果")

    # 处理下载结果
    final_path = f"{selected_path}/{resource_name}"
    if init.openapi_115.is_directory(final_path):
        # 如果下载的内容是目录，清除垃圾文件
        init.openapi_115.auto_clean(final_path)
    else:
        # 批量下载时(>=2个链接)，不套temp文件夹，直接使用文件本身
        # 单个下载时才套temp文件夹
        if total_count < 2:
            # 如果下载的内容是文件，为文件套一个文件夹
            temp_folder = "temp"
            init.openapi_115.create_dir_for_file(selected_path, temp_folder)
            # 移动文件到临时目录
            init.openapi_115.move_file(f"{selected_path}/{resource_name}", f"{selected_path}/{temp_folder}")
            final_path = f"{selected_path}/{temp_folder}"
            resource_name = temp_folder

    # 获取文件列表并创建STRM文件
    file_list = init.openapi_115.get_files_from_dir(final_path)
    create_strm_file(final_path, file_list)

    # 注意：批量下载时不在这里调用 notice_emby_scan_library()
    # 会在所有任务处理完成后统一调用一次

    # 返回最终的资源名称（用于批量移动功能）
    return resource_name


def process_successful_download(link, selected_path, user_id, resource_name, task_index, total_count):
    """处理下载成功的任务，返回最终的资源名称（用于批量移动）"""
    from app.utils.message_queue import add_task_to_queue

    progress_info = f"[{task_index}/{total_count}]"
    init.logger.info(f"{progress_info} {resource_name} 开始处理下载结果")

    # 保存原始资源名用于显示
    original_resource_name = resource_name

    # 处理下载结果
    final_path = f"{selected_path}/{resource_name}"
    if init.openapi_115.is_directory(final_path):
        # 如果下载的内容是目录，清除垃圾文件
        init.openapi_115.auto_clean(final_path)
    else:
        # 批量下载时(>=2个链接)，不套temp文件夹，直接使用文件本身
        # 单个下载时才套temp文件夹
        if total_count < 2:
            # 如果下载的内容是文件，为文件套一个文件夹
            temp_folder = "temp"
            init.openapi_115.create_dir_for_file(selected_path, temp_folder)
            # 移动文件到临时目录
            init.openapi_115.move_file(f"{selected_path}/{resource_name}", f"{selected_path}/{temp_folder}")
            final_path = f"{selected_path}/{temp_folder}"
            resource_name = temp_folder

    # 获取文件列表并创建STRM文件
    file_list = init.openapi_115.get_files_from_dir(final_path)
    create_strm_file(final_path, file_list)

    # 当有2个及以上链接时，跳过TMDB名称指定提示，直接完成
    if total_count >= 2:
        # 批量下载时不提示重命名，直接通知下载完成
        progress_text = f"{progress_info} "
        # 使用原始资源名显示，而不是处理后的名称（如temp）
        message = f"✅ {progress_text}电影\\[`{original_resource_name}`\\]离线下载完成\\!"

        # 通知Emby扫库
        notice_emby_scan_library()

        add_task_to_queue(user_id, None, message=message)
        init.logger.info(f"{progress_info} 批量下载，跳过重命名提示")

        # 返回最终的资源名称（用于批量移动功能）
        return resource_name
    else:
        # 单个链接时，提供重命名选项
        # 为避免callback_data长度限制，使用时间戳作为唯一标识符
        task_id = str(int(time.time() * 1000))  # 毫秒时间戳作为唯一ID

        # 将任务数据存储到全局字典中（临时存储）
        if not hasattr(init, 'pending_tasks'):
            init.pending_tasks = {}

        init.pending_tasks[task_id] = {
            "user_id": user_id,
            "action": "manual_rename",
            "final_path": final_path,
            "resource_name": resource_name,
            "selected_path": selected_path,
            "link": link,
            "add2retry": False
        }

        # 发送下载成功通知，包含选择按钮
        keyboard = [
            [InlineKeyboardButton("指定标准的TMDB名称", callback_data=f"rename_{task_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message = f"✅ 电影\\[`{resource_name}`\\]离线下载完成\\!\n\n便于削刮，请为资源指定TMDB的标准名称！"

        add_task_to_queue(user_id, None, message=message, keyboard=reply_markup)


def process_failed_download_silent(link, selected_path, user_id, resource_name, task_index, total_count):
    """
    处理下载失败的任务（静默版本，不发送消息）
    用于批量下载时的进度跟踪
    """
    progress_info = f"[{task_index}/{total_count}]"

    # 下载超时，删除任务
    init.openapi_115.clear_failed_task(link)
    timeout_message = f"❌ {progress_info} {resource_name} 离线下载超时" if resource_name else f"❌ {progress_info} 离线下载超时"
    init.logger.warn(timeout_message)

    # 批量下载失败时，直接添加到重试列表
    title = format_retry_task_name(resource_name, link, link_count=1)
    save_failed_download_to_db(title, link, selected_path)
    init.logger.info(f"{progress_info} 批量下载失败，已自动添加到重试列表")


def process_failed_download(link, selected_path, user_id, resource_name, task_index, total_count):
    """处理下载失败的任务"""
    from app.utils.message_queue import add_task_to_queue

    progress_info = f"[{task_index}/{total_count}]"

    # 下载超时，删除任务并提供选择
    init.openapi_115.clear_failed_task(link)
    timeout_message = f"❌ {progress_info} {resource_name} 离线下载超时" if resource_name else f"❌ {progress_info} 离线下载超时"
    init.logger.warn(timeout_message)

    # 当有2个及以上链接时，跳过重命名提示，直接添加到重试列表
    if total_count >= 2:
        # 批量下载失败时，直接添加到重试列表，不提示用户
        title = format_retry_task_name(resource_name, link, link_count=1)
        save_failed_download_to_db(title, link, selected_path)

        failure_text = f"{progress_info} "
        # 截断过长的链接用于显示
        display_link = link[:100] + "..." if len(link) > 100 else link
        message = f"❌ {failure_text}\\[`{resource_name}`\\] 离线下载超时\n\n已自动添加到重试列表"

        add_task_to_queue(user_id, None, message=message)
        init.logger.info(f"{progress_info} 批量下载失败，已自动添加到重试列表")
    else:
        # 单个链接失败时，提供重命名并添加到重试列表的选项
        # 为失败重试也使用时间戳ID
        retry_task_id = str(int(time.time() * 1000))

        # 将重试任务数据存储到全局字典中
        if not hasattr(init, 'pending_tasks'):
            init.pending_tasks = {}

        init.pending_tasks[retry_task_id] = {
            "user_id": user_id,
            "action": "retry_download",
            "selected_path": selected_path,
            "resource_name": resource_name if resource_name else "未知资源",
            "link": link,
            "add2retry": True
        }

        # 提供重试选项
        keyboard = [
            [InlineKeyboardButton("指定TMDB名称并添加到重试列表", callback_data=f"rename_{retry_task_id}")],
            [InlineKeyboardButton("取消", callback_data="cancel_download")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # 截断过长的链接
        display_link = link[:600] + "..." if len(link) > 600 else link
        message = f"`{display_link}`\n\n😭 离线下载超时，请选择后续操作："

        add_task_to_queue(user_id, None, message=message, keyboard=reply_markup)


def download_tasks_batch(links, selected_path, user_id, custom_folder_name="", reaction_targets=None):
    """批量下载任务处理（减少API调用频率，实时更新磁力明细并在完成时引用原消息通知）"""
    import uuid
    from telegram.helpers import escape_markdown
    from app.utils.message_queue import add_task_to_queue

    total_count = len(links)
    init.logger.info(f"开始批量处理 {total_count} 个下载任务")
    tracker_id = f"dl_{uuid.uuid4().hex[:8]}"
    task_title = custom_folder_name or (f"批量下载({total_count}个)" if total_count > 1 else "单链接离线下载")

    origin_chat_id = user_id
    origin_message_id = None
    if reaction_targets and isinstance(reaction_targets[0], (tuple, list)) and len(reaction_targets[0]) == 2:
        origin_chat_id, origin_message_id = reaction_targets[0]

    initial_items = [
        {"index": idx, "link": item.get("link", ""), "status": "pending", "percent": 0}
        for idx, item in enumerate(links, 1)
    ]

    register_task(
        tracker_id,
        task_type="download",
        title=task_title,
        total=total_count,
        save_path=selected_path,
        user_id=user_id,
        stage="提交离线任务",
        items=initial_items,
        chat_id=origin_chat_id,
        origin_message_id=origin_message_id,
    )

    succeeded = False
    try:
        _download_tasks_batch_impl(
            links,
            selected_path,
            user_id,
            custom_folder_name,
            tracker_id=tracker_id,
            origin_message_id=origin_message_id,
        )
        succeeded = True
        finish_task(tracker_id, status="completed", note="已完成")
    except Exception as e:
        init.logger.error(f"批量下载任务执行失败: {e}", exc_info=True)
        finish_task(tracker_id, status="failed", note=str(e)[:40])
        err_msg = escape_markdown(f"❌ 批量下载任务执行失败: {str(e)}\n请查看日志获取详细信息", version=2)
        add_task_to_queue(
            user_id,
            f"{init.IMAGE_PATH}/male023.png",
            message=err_msg,
            reply_to_message_id=origin_message_id,
        )
    finally:
        # 对话内跟踪结束后仍未出结果（提交失败/查询异常）的项转入后台自动重试
        try:
            from app.utils.task_tracker import get_task as _get_tracked_task
            from app.core.batch_task_manager import mark_pending_items_failed
            linked_gid = (_get_tracked_task(tracker_id) or {}).get("batch_group_id")
            if linked_gid:
                mark_pending_items_failed(linked_gid)
        except Exception as e:
            init.logger.warn(f"转入后台重试失败: {e}")
        if reaction_targets:
            schedule_reactions_batch(reaction_targets, REACTION_DONE if succeeded else REACTION_ERROR)


def _download_tasks_batch_impl(links, selected_path, user_id, custom_folder_name="", tracker_id=None, origin_message_id=None):
    """批量下载任务的实际实现（内部函数）"""
    from app.utils.message_queue import add_task_to_queue
    from app.core.batch_task_manager import (
        generate_batch_folder_name,
        create_batch_task_group,
        save_batch_task_items,
        update_task_item_status,
        update_task_group_counts,
        generate_magnet_txt_file
    )

    total_count = len(links)
    task_created_at = int(time.time())
    init.logger.info(f"开始执行批量下载任务实现，共 {total_count} 个任务")

    # === 批量任务管理 ===
    batch_group_id = None
    final_folder_name = custom_folder_name

    # 批量任务（>1个链接）且没有自定义文件夹名时，生成默认名称
    if total_count > 1 and not custom_folder_name:
        final_folder_name = generate_batch_folder_name(total_count)
        init.logger.info(f"🎯 生成默认批量任务文件夹名: {final_folder_name}")

    # 所有对话任务（含单链接）统一创建任务组记录：用于任务队列展示与后台 2 倍退避自动重试
    batch_group_id = create_batch_task_group(
        final_folder_name if total_count > 1 else "",
        selected_path,
        total_count,
        chat_id=user_id,
        origin_message_id=origin_message_id,
    )

    # 保存任务项
    save_batch_task_items(batch_group_id, links)
    if tracker_id:
        update_task(tracker_id, batch_group_id=batch_group_id)

    # 如果有文件夹名且链接数>1,创建文件夹
    actual_download_path = selected_path
    if final_folder_name and total_count > 1:
        # 在选择的路径下创建文件夹
        try:
            init.openapi_115.create_dir_for_file(selected_path, final_folder_name)
            actual_download_path = f"{selected_path}/{final_folder_name}"
            init.logger.info(f"创建批量任务文件夹: {actual_download_path}")
        except Exception as e:
            init.logger.error(f"创建文件夹失败: {e}")
            # 如果创建失败,使用原路径
            actual_download_path = selected_path

    # 批量提交模式开关（设为True启用批量提交，False使用原有循环提交）
    USE_BATCH_SUBMIT = True
    # 115 API 限制每次最多提交 115 个任务
    MAX_BATCH_SIZE = 115
    BATCH_INTERVAL = 1  # 批次间隔时间（秒）

    if USE_BATCH_SUBMIT:
        # 新方案：分批提交链接（每批最多 115 个）
        all_links = [link_info["link"] for link_info in links]
        init.logger.info(f"使用批量提交模式，共 {total_count} 个链接")

        # 分批处理
        submitted_tasks = []
        batch_count = (total_count + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE  # 向上取整
        init.logger.info(f"需要分 {batch_count} 批次提交，每批最多 {MAX_BATCH_SIZE} 个任务")

        try:
            for batch_idx in range(batch_count):
                start_idx = batch_idx * MAX_BATCH_SIZE
                end_idx = min(start_idx + MAX_BATCH_SIZE, total_count)
                batch_links = all_links[start_idx:end_idx]
                batch_size = len(batch_links)

                init.logger.info(f"正在提交第 {batch_idx + 1}/{batch_count} 批，包含 {batch_size} 个任务")

                # 提交当前批次
                offline_success = init.openapi_115.offline_download_specify_path_batch(
                    batch_links, actual_download_path
                )

                # 记录每个链接的提交结果
                for i in range(start_idx, end_idx):
                    link_info = links[i]
                    submitted_tasks.append({
                        "link": link_info["link"],
                        "index": i + 1,
                        "submitted": offline_success
                    })

                if offline_success:
                    init.logger.info(f"第 {batch_idx + 1}/{batch_count} 批提交成功：{batch_size} 个任务已添加")
                else:
                    init.logger.warn(f"第 {batch_idx + 1}/{batch_count} 批提交失败")
                    # 失败的批次也记录，但不中断后续批次

                # 批次间隔，避免请求过快（最后一批不需要等待）
                if batch_idx < batch_count - 1:
                    init.logger.info(f"等待 {BATCH_INTERVAL} 秒后提交下一批...")
                    time.sleep(BATCH_INTERVAL)

            # 检查是否有任何成功提交的任务
            success_count = sum(1 for task in submitted_tasks if task["submitted"])
            if success_count > 0:
                init.logger.info(f"批量提交完成：共 {success_count}/{total_count} 个任务提交成功")
            else:
                # 所有批次都失败，通知用户
                error_message = f"❌ 批量提交 {total_count} 个离线任务全部失败！"
                add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png", message=error_message)
                init.logger.warn(f"所有批次提交失败")

        except Exception as e:
            init.logger.error(f"批量提交异常: {e}")
            # 异常情况，所有未提交的任务标记为失败
            if not submitted_tasks:
                submitted_tasks = []
                for i, link_info in enumerate(links, 1):
                    submitted_tasks.append({
                        "link": link_info["link"],
                        "index": i,
                        "submitted": False
                    })
            error_message = f"❌ 批量提交异常: {str(e)}"
            add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png", message=error_message)

    else:
        # 原方案：循环逐个提交（保留以便回滚）
        submitted_tasks = []
        for i, link_info in enumerate(links, 1):
            link = link_info["link"]
            init.logger.info(f"[{i}/{total_count}] 提交离线任务: {link[:80]}...")

            offline_success = init.openapi_115.offline_download_specify_path(link, actual_download_path)
            if offline_success:
                submitted_tasks.append({
                    "link": link,
                    "index": i,
                    "submitted": True
                })
            else:
                error_message = f"❌ [{i}/{total_count}] 提交离线任务失败！"
                add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png", message=error_message)
                submitted_tasks.append({
                    "link": link,
                    "index": i,
                    "submitted": False
                })
            time.sleep(2)  # 提交间隔2秒

    if not submitted_tasks:
        init.logger.error("所有任务提交失败")
        return

    # 记录提交前下载目录的文件列表（用于后续对比）
    files_before_download = set()
    try:
        parent_info = init.openapi_115.get_file_info(actual_download_path)
        if parent_info:
            params = {
                "cid": parent_info['file_id'],
                "limit": 1000,
                "show_dir": 1
            }
            file_list_before = init.openapi_115.get_file_list(params)
            if file_list_before:
                files_before_download = {f.get('fn', '') for f in file_list_before}
                init.logger.info(f"提交前下载目录有 {len(files_before_download)} 个文件")
    except Exception as e:
        init.logger.warn(f"获取提交前文件列表失败: {e}")

    # 第二步：短暂初始等待（3秒），让 115 秒传/热种任务第一时间出结果
    from app.utils.poll_scheduler import (
        AdaptivePollController,
        INITIAL_WAIT_SECONDS,
        DEFAULT_STALL_TIMEOUT_SECONDS,
    )

    initial_wait = INITIAL_WAIT_SECONDS
    init.logger.info(f"已提交 {len(submitted_tasks)} 个任务，等待 {initial_wait:.0f} 秒后开始按需轮询状态...")
    time.sleep(initial_wait)

    # 第三步：按需分页 + 基于 ETA（预计剩余完成时间）自适应轮询 + 10分钟无进度早停
    submitted_links = [t["link"] for t in submitted_tasks if t["submitted"]]
    poll_controller = AdaptivePollController(stall_timeout=DEFAULT_STALL_TIMEOUT_SECONDS)
    max_total_time = 3600  # 最长总等待时间 1 小时（持续有进度时才会拉满）
    poll_start_ts = time.monotonic() - initial_wait
    poll_round = 0
    all_completed = False
    early_stopped_stalled = False
    offline_task_status = None

    while (time.monotonic() - poll_start_ts) < max_total_time:
        poll_round += 1
        total_elapsed = int(time.monotonic() - poll_start_ts)
        init.logger.info(f"第 {poll_round} 次检查任务状态（已等待 {total_elapsed}秒）...")

        try:
            start_time = time.time()
            # 按需分页查询：命中本批链接立即停翻页（通常仅 1 次 API 请求）
            offline_task_status = init.openapi_115.get_offline_tasks_for_links(
                submitted_links,
                use_cache=(poll_round > 1),
            )
            elapsed = time.time() - start_time
            init.logger.info(f"按需获取任务列表耗时: {elapsed:.2f}秒，返回 {len(offline_task_status) if offline_task_status else 0} 个任务")
        except Exception as e:
            init.logger.error(f"获取离线任务状态时出错: {e}")
            offline_task_status = None

        if offline_task_status is None:
            init.logger.error("无法获取离线任务状态")
            if poll_round == 1:  # 第一次就失败才报错
                add_task_to_queue(
                    user_id,
                    f"{init.IMAGE_PATH}/male023.png",
                    message="❌ 无法获取离线任务状态，请稍后手动检查",
                    reply_to_message_id=origin_message_id,
                )
                return
            else:
                break

        # 构建 url -> task 索引，避免 O(N*M) 遍历
        cloud_by_url = {
            t.get('url'): t for t in offline_task_status if isinstance(t, dict) and t.get('url')
        }

        # 检查所有提交的任务是否都已完成，同时收集未完成任务的实时百分比用于 ETA 计算
        init.logger.info(f"开始检查 {len(submitted_tasks)} 个提交任务的状态...")
        completed_count = 0
        processing_count = 0
        active_percents = {}

        for task_info in submitted_tasks:
            if not task_info["submitted"]:
                continue

            link = task_info["link"]
            task = cloud_by_url.get(link)
            if task is not None:
                task_status = task.get('status')
                task_percent = task.get('percentDone') or 0
                task_name = task.get('name', 'unknown')

                # status=2 或 percentDone=100 视为完成；status=-1 视为失败
                if task_percent == 100 or task_status == 2:
                    completed_count += 1
                    if tracker_id:
                        update_task_item(tracker_id, task_info["index"], name=task_name, status="success", percent=100)
                elif task_status == -1:
                    completed_count += 1
                    if tracker_id:
                        update_task_item(tracker_id, task_info["index"], name=task_name, status="failed", percent=task_percent, error="115离线失败")
                else:
                    processing_count += 1
                    active_percents[link] = float(task_percent)
                    if tracker_id:
                        update_task_item(tracker_id, task_info["index"], name=task_name, status="running", percent=task_percent)
                    if poll_round == 1 or processing_count <= 5:
                        init.logger.info(f"[下载中] {task_name}: {task_percent}%")
            else:
                init.logger.warn(f"任务未在云端列表中找到（可能已完成被清理或已失效）: {link[:50]}...")
                completed_count += 1

        submitted_count = len(submitted_links)
        init.logger.info(f"进度: 已完成 {completed_count}/{submitted_count}, 下载中 {processing_count}/{submitted_count}")
        if tracker_id:
            update_task(tracker_id, stage=f"轮询离线状态 ({completed_count}/{submitted_count})")

        # 如果所有任务都已完成或失败，立刻退出轮询
        if completed_count >= submitted_count or not active_percents:
            all_completed = True
            init.logger.info("所有任务已完成，停止轮询")
            break

        # 根据 ETA 与停滞时长计算下次检查等待时间
        current_wait, should_early_stop, poll_reason = poll_controller.next_wait(active_percents)
        if should_early_stop:
            early_stopped_stalled = True
            init.logger.warn(f"⏸️ 触发停滞早停：{poll_reason}，提前结束等待并转入后台重试")
            break

        total_elapsed = time.monotonic() - poll_start_ts
        if total_elapsed + current_wait >= max_total_time:
            init.logger.warn(f"即将达到最大等待时间（{max_total_time}秒），停止轮询")
            break

        init.logger.info(f"还有 {processing_count} 个任务未完成（{poll_reason}），等待 {current_wait:.0f} 秒后继续检查...")
        time.sleep(current_wait)

    total_elapsed = int(time.monotonic() - poll_start_ts)
    init.logger.info(f"轮询结束，all_completed={all_completed}, early_stopped_stalled={early_stopped_stalled}, total_elapsed={total_elapsed}秒")

    if not all_completed:
        init.logger.warn(f"轮询提前/超时结束({total_elapsed}秒)，仍有任务未完成，将汇总已完成项并把未完成项交由后台自动重试")

    # 使用最后一次获取的任务状态
    if offline_task_status is None:
        init.logger.error("无任务状态可用，退出处理")
        return

    # 第四步：批量处理每个任务的结果（不再发送重复的旧版"正在进行的进度清单"消息，统一由最终完成通知汇报）
    successful_downloads = []  # 记录成功下载的资源
    task_results = []  # 记录所有任务结果用于统计与错误日志生成

    # 第一阶段：检查所有任务的下载状态
    init.logger.info("第一阶段：检查所有任务的下载状态...")

    # 获取下载目录当前的所有文件（用于判断"任务未找到"的情况）
    files_after_download = {}
    new_files = []
    try:
        parent_info = init.openapi_115.get_file_info(actual_download_path)
        if parent_info:
            params = {
                "cid": parent_info['file_id'],
                "limit": 1000,
                "show_dir": 1
            }
            file_list = init.openapi_115.get_file_list(params)
            if file_list:
                for f in file_list:
                    file_name = f.get('fn', '')
                    files_after_download[file_name] = f
                    # 如果是新文件（不在提交前的列表中）
                    if file_name not in files_before_download:
                        new_files.append(file_name)
                init.logger.info(f"下载目录现有 {len(files_after_download)} 个文件，新增 {len(new_files)} 个")
    except Exception as e:
        init.logger.warn(f"获取下载目录文件列表失败: {e}")

    # 收集失败的任务链接，稍后批量清理（避免在循环中重复调用 get_offline_tasks）
    failed_task_links = []

    for task_info in submitted_tasks:
        if not task_info["submitted"]:
            continue

        link = task_info["link"]
        task_index = task_info["index"]
        progress_info = f"[{task_index}/{total_count}]"

        # 在API返回的任务列表中查找匹配的任务
        download_success = False
        resource_name = ""
        task_found_in_list = False

        for task in offline_task_status:
            if task.get('url') == link:
                task_found_in_list = True
                resource_name = task.get('name', '')
                task_status = task.get('status')
                task_percent = task.get('percentDone')

                # 关键修复：percentDone=100时也视为成功（即使status还是1）
                if task_percent == 100:
                    download_success = True
                    init.logger.info(f"{progress_info} {resource_name} 离线下载成功！")
                else:
                    init.logger.warn(f"{progress_info} {resource_name} 离线下载超时或失败 (status={task_status}, percentDone={task_percent})")
                break

        # 如果任务不在列表中，可能已完成并被清理，检查下载目录的新文件
        if not task_found_in_list:
            init.logger.info(f"{progress_info} 任务不在列表中（可能已完成被清理），检查新下载的文件...")

            if len(new_files) == 0:
                # 没有新文件，可能真的失败了
                init.logger.warn(f"{progress_info} 未找到新下载的文件，标记为失败")
            else:
                # 从链接中提取期望的文件名
                expected_name = ""
                if link.startswith('ed2k://'):
                    parts = link.split('|')
                    if len(parts) >= 3:
                        expected_name = parts[2]

                # 先尝试精确匹配
                if expected_name and expected_name in new_files:
                    download_success = True
                    resource_name = expected_name
                    init.logger.info(f"{progress_info} 找到已下载文件（精确匹配）: {resource_name}")
                # 再尝试模糊匹配
                elif expected_name:
                    for new_file in new_files:
                        # 去掉可能的前缀（如 www.98T.la@）进行匹配
                        clean_expected = expected_name.split('@')[-1] if '@' in expected_name else expected_name
                        clean_new_file = new_file.split('@')[-1] if '@' in new_file else new_file

                        # 检查是否匹配（双向包含，处理可能的文件名变化）
                        if clean_expected.lower() in clean_new_file.lower() or clean_new_file.lower() in clean_expected.lower():
                            download_success = True
                            resource_name = new_file
                            new_files.remove(new_file)  # 从列表中移除已匹配的文件，避免重复匹配
                            init.logger.info(f"{progress_info} 找到已下载文件（模糊匹配）: {resource_name}")
                            break
                # 对于magnet或无法提取文件名的情况，尝试匹配剩余的新文件
                elif len(new_files) > 0:
                    resource_name = new_files.pop(0)  # 取第一个未匹配的新文件
                    download_success = True
                    init.logger.info(f"{progress_info} 无法精确匹配，使用新文件: {resource_name}")

        if download_success:
            # 只记录成功的任务，稍后批量处理
            successful_downloads.append({
                "resource_name": resource_name,
                "link": link,
                "index": task_index
            })

            # 记录成功结果用于进度显示
            task_results.append({
                "index": task_index,
                "status": "success",
                "name": resource_name,
                "link": link
            })
            if tracker_id:
                update_task_item(tracker_id, task_index, name=resource_name or None, status="success", percent=100)

            # 如果是批量任务，更新批量任务状态
            if batch_group_id:
                from app.core.batch_task_manager import update_task_item_status
                update_task_item_status(link, batch_group_id, 'success', resource_name)
        else:
            # 失败的任务直接添加到重试列表（未提取到文件名时使用 yyyyMMDDHHmmss-磁力链接）
            title = format_retry_task_name(
                resource_name,
                link,
                created_at=task_created_at,
                link_count=1,
            )

            # 如果是批量任务，更新批量任务状态（标记为failed，等待自动重试）
            if batch_group_id:
                from app.core.batch_task_manager import update_task_item_status
                update_task_item_status(link, batch_group_id, 'failed', title)
            else:
                # 非批量任务，添加到旧的重试列表
                save_failed_download_to_db(title, link, actual_download_path)

            # 收集失败任务的链接，稍后批量清理
            failed_task_links.append(link)
            init.logger.info(f"{progress_info} 已添加到重试列表")

            # 记录失败结果
            task_results.append({
                "index": task_index,
                "status": "failed",
                "name": resource_name,
                "link": link
            })
            if tracker_id:
                update_task_item(tracker_id, task_index, name=resource_name or None, status="failed", error="离线超时或失败")

        # 实时同步任务队列统计（不再重复发送或编辑旧版进度消息）
        if tracker_id and (len(task_results) % 10 == 0 or len(task_results) == len(submitted_tasks)):
            done_cnt = len([t for t in task_results if t.get("status") == "success"])
            fail_cnt = len([t for t in task_results if t.get("status") == "failed"])
            update_task(tracker_id, completed=done_cnt, failed=fail_cnt, stage="整理离线结果")

    # 第一阶段结束：批量清理离线任务记录（直接复用本轮已获取的 info_hash，避免再次全量拉取云端列表）
    successful_task_links = [d["link"] for d in successful_downloads]

    if failed_task_links or (all_completed and successful_task_links):
        cleanup_links = []
        if failed_task_links:
            cleanup_links.extend(failed_task_links)
            init.logger.info(f"准备清理 {len(failed_task_links)} 个失败任务")
        if all_completed and successful_task_links:
            cleanup_links.extend(successful_task_links)
            init.logger.info(f"所有任务已完成，准备清理 {len(successful_task_links)} 个成功任务")

        if cleanup_links:
            if tracker_id:
                update_task(tracker_id, stage="清理离线任务记录")
            init.logger.info(f"开始批量清理 {len(cleanup_links)} 个离线任务...")
            try:
                hash_by_url = {
                    t.get('url'): t.get('info_hash', '')
                    for t in (offline_task_status or [])
                    if isinstance(t, dict) and t.get('url') and t.get('info_hash')
                }
                missing_links = [u for u in cleanup_links if u not in hash_by_url]
                if missing_links:
                    refreshed = init.openapi_115.get_offline_tasks_for_links(missing_links, use_cache=False) or []
                    for t in refreshed:
                        if isinstance(t, dict) and t.get('url') and t.get('info_hash'):
                            hash_by_url[t['url']] = t['info_hash']

                cleaned_count = 0
                failed_link_set = set(failed_task_links)
                for link in cleanup_links:
                    info_hash = hash_by_url.get(link)
                    if info_hash:
                        delete_source = link in failed_link_set
                        init.openapi_115.del_faild_offline_task(info_hash, delete_source_file=delete_source)
                        cleaned_count += 1
                        init.logger.debug(f"已清理任务: {link[:50]}...")
                init.logger.info(f"批量清理完成，共清理 {cleaned_count}/{len(cleanup_links)} 个离线任务")
            except Exception as e:
                init.logger.error(f"批量清理离线任务时出错: {e}")
    else:
        if not all_completed:
            init.logger.info(f"仍有任务未完成，保留所有离线任务记录（包括 {len(successful_task_links)} 个成功任务）")

    # 第二阶段：批量处理所有成功下载的文件（优化版）
    init.logger.info(f"第二阶段：开始批量处理 {len(successful_downloads)} 个成功下载的文件...")
    final_resource_names = []

    # 提取所有资源名称
    resource_names = [d["resource_name"] for d in successful_downloads]

    # 使用批量API一次性获取所有文件信息（大幅减少API调用）
    try:
        # 批量获取文件信息和目录结构
        batch_result = init.openapi_115.batch_process_downloads(actual_download_path, resource_names)

        # 批量清理垃圾文件（一次性处理所有目录）
        init.openapi_115.batch_auto_clean(actual_download_path, resource_names)

        # 处理每个资源
        for idx, download_info in enumerate(successful_downloads, 1):
            resource_name = download_info["resource_name"]
            progress_info = f"[{idx}/{len(successful_downloads)}]"

            try:
                init.logger.info(f"{progress_info} 处理文件: {resource_name}")

                if resource_name in batch_result:
                    resource_data = batch_result[resource_name]
                    final_path = resource_data["path"]
                    file_list = resource_data["files"]

                    # 创建STRM文件
                    create_strm_file(final_path, file_list)

                    final_resource_names.append(resource_name)
                    init.logger.info(f"{progress_info} 处理完成")
                else:
                    init.logger.warn(f"{progress_info} 未找到资源信息: {resource_name}")
                    final_resource_names.append(resource_name)  # 即使失败也记录

            except Exception as e:
                init.logger.error(f"{progress_info} 处理文件失败: {e}")
                final_resource_names.append(resource_name)  # 即使失败也记录，用于移动功能

    except Exception as e:
        init.logger.error(f"批量处理失败，回退到逐个处理: {e}")
        # 回退方案：逐个处理
        for idx, download_info in enumerate(successful_downloads, 1):
            resource_name = download_info["resource_name"]
            progress_info = f"[{idx}/{len(successful_downloads)}]"

            try:
                init.logger.info(f"{progress_info} 处理文件（回退模式）: {resource_name}")

                # 处理下载结果
                final_path = f"{actual_download_path}/{resource_name}"
                if init.openapi_115.is_directory(final_path):
                    # 如果下载的内容是目录，清除垃圾文件
                    init.openapi_115.auto_clean(final_path)

                # 获取文件列表并创建STRM文件
                file_list = init.openapi_115.get_files_from_dir(final_path)
                create_strm_file(final_path, file_list)

                final_resource_names.append(resource_name)
                init.logger.info(f"{progress_info} 处理完成")
            except Exception as e:
                init.logger.error(f"{progress_info} 处理文件失败: {e}")
                final_resource_names.append(resource_name)

    # 第五步：离线任务已在第一阶段逐个清理（仅清理当前批量任务相关的任务）
    init.logger.info(f"批量下载任务处理完成，共 {total_count} 个任务")

    # 批量下载完成后，统一通知 Emby 扫库一次（而不是每个文件都通知）
    if len(final_resource_names) > 0:
        notice_emby_scan_library()
        init.logger.info("已通知 Emby 扫库（批量下载完成）")

    # === 批量任务后处理 ===
    if batch_group_id:
        # 更新批量任务组统计
        from app.core.batch_task_manager import update_task_group_counts, generate_magnet_txt_file
        update_task_group_counts(batch_group_id)
        init.logger.info(f"✅ 批量任务组 {batch_group_id} 统计已更新")

        # 生成磁力链接TXT文件
        try:
            txt_success = (
                generate_magnet_txt_file(batch_group_id, actual_download_path, final_folder_name)
                if total_count > 1 else True
            )
            if txt_success:
                init.logger.info(f"📄 批量任务磁力链接TXT文件已生成")
            else:
                init.logger.warn(f"⚠️ 批量任务磁力链接TXT文件生成失败")
        except Exception as e:
            init.logger.error(f"❌ 生成磁力链接TXT文件异常: {e}")

    # 第六步（合并原第六、七步）：统一只发送 1 条引用原消息的完成结果通知（附带查看本次任务明细按钮）
    from telegram.helpers import escape_markdown
    from app.utils.message_queue import send_document_via_bot

    failed_count = len([t for t in task_results if t.get("status") == "failed"])
    success_count = len([t for t in task_results if t.get("status") == "success"])
    success_total = len(final_resource_names)
    detail_markup = (
        InlineKeyboardMarkup([[InlineKeyboardButton("🔍 查看本次任务明细", callback_data=f"panel:td:{tracker_id}:1:all")]])
        if tracker_id
        else None
    )
    stall_hint = "（部分任务连续10分钟无进度已转后台自动重试）" if early_stopped_stalled else ""

    sent_merged_error_doc = False
    if failed_count > 5:
        error_log_path = None
        try:
            error_log_path = generate_error_log_file(task_results, total_count, custom_folder_name, batch_group_id)
            if error_log_path:
                created_at = None
                if batch_group_id:
                    from app.core.batch_task_manager import get_batch_task_group_info
                    group_info = get_batch_task_group_info(batch_group_id)
                    if group_info and 'created_at' in group_info:
                        try:
                            created_at = int(group_info['created_at'])
                        except Exception as e:
                            init.logger.warn(f"无法解析创建时间: {group_info['created_at']}, 错误: {e}")
                            created_at = None

                caption = generate_batch_summary_caption(
                    custom_folder_name or final_folder_name,
                    batch_group_id,
                    failed_count,
                    success_count,
                    total_count,
                    created_at
                )
                if actual_download_path:
                    caption += f"\n📂 保存路径: {actual_download_path}"
                if stall_hint:
                    caption += f"\n⏸️ {stall_hint}"

                send_document_via_bot(
                    chat_id=user_id,
                    file_path=error_log_path,
                    caption=caption[:1024],
                    reply_to_message_id=origin_message_id,
                    reply_markup=detail_markup,
                )
                sent_merged_error_doc = True
                init.logger.info(f"已发送合并后的错误日志与完成汇总（{failed_count}个失败任务，引用原消息={origin_message_id}）")
        except Exception as e:
            init.logger.error(f"发送合并错误日志文件失败，将回退为文本汇总: {e}")
        finally:
            if error_log_path and os.path.exists(error_log_path):
                try:
                    os.remove(error_log_path)
                except Exception:
                    pass

    if not sent_merged_error_doc:
        safe_folder = escape_markdown(str(custom_folder_name or final_folder_name or ""), version=2)
        safe_path = escape_markdown(str(actual_download_path or selected_path or ""), version=2)

        if total_count > 1:
            if success_total == total_count:
                header = escape_markdown(f"✅ 批量下载完成！共 {success_total}/{total_count} 个任务全部成功", version=2)
            elif success_total > 0:
                header = escape_markdown(
                    f"⚠️ 批量下载完成！成功 {success_total}/{total_count}，失败 {failed_count} 个{stall_hint or '（已加入自动重试队列）'}",
                    version=2,
                )
            else:
                header = escape_markdown(
                    f"❌ 批量下载结束！0/{total_count} 个任务成功（{failed_count} 个未完成任务已加入自动重试队列）{stall_hint}",
                    version=2,
                )
            folder_line = f"\n\n📁 保存路径: `{safe_path}`" if safe_path else (f"\n\n📁 文件夹: `{safe_folder}`" if safe_folder else "")
            message = f"{header}{folder_line}"
        else:
            if success_total > 0:
                resource_name = escape_markdown(str(final_resource_names[0]), version=2)
                header = escape_markdown("✅ 下载完成！", version=2)
                message = f"{header}\n\n📁 文件名: `{resource_name}`\n📂 保存路径: `{safe_path}`"
            else:
                header = escape_markdown(
                    f"❌ 离线下载超时或失败{stall_hint}，已加入任务队列，将按等待时间翻倍自动重试。",
                    version=2,
                )
                message = f"{header}\n\n📂 目标路径: `{safe_path}`"

        add_task_to_queue(
            user_id,
            None,
            message=message,
            keyboard=detail_markup,
            reply_to_message_id=origin_message_id,
        )


def download_task(link, selected_path, user_id, task_index=1, total_tasks=1):
    """异步下载任务（单个任务使用，已弃用，保留用于兼容）"""
    from app.utils.message_queue import add_task_to_queue

    try:
        # 添加任务进度信息到日志和通知
        progress_info = f"[{task_index}/{total_tasks}]" if total_tasks > 1 else ""
        init.logger.info(f"开始处理下载任务 {progress_info}: {link}")

        offline_success = init.openapi_115.offline_download_specify_path(link, selected_path)
        if not offline_success:
            error_message = f"❌ {progress_info} 离线遇到错误！" if progress_info else "❌ 离线遇到错误！"
            add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png", message=error_message)
            return

        # 检查下载状态
        download_success, resource_name = init.openapi_115.check_offline_download_success(link)

        if download_success:
            success_message = f"✅ {progress_info} {resource_name} 离线下载成功！" if progress_info else f"✅ {resource_name} 离线下载成功！"
            init.logger.info(success_message)
            time.sleep(1)
            
            # 处理下载结果
            final_path = f"{selected_path}/{resource_name}"
            if init.openapi_115.is_directory(final_path):
                # 如果下载的内容是目录，清除垃圾文件
                init.openapi_115.auto_clean(final_path)
            else:
                # 如果下载的内容是文件，为文件套一个文件夹
                temp_folder = "temp"
                init.openapi_115.create_dir_for_file(selected_path, temp_folder)
                # 移动文件到临时目录
                init.openapi_115.move_file(f"{selected_path}/{resource_name}", f"{selected_path}/{temp_folder}")
                final_path = f"{selected_path}/{temp_folder}"
                resource_name = temp_folder
            
            # 为避免callback_data长度限制，使用时间戳作为唯一标识符
            task_id = str(int(time.time() * 1000))  # 毫秒时间戳作为唯一ID
            
            # 将任务数据存储到全局字典中（临时存储）
            if not hasattr(init, 'pending_tasks'):
                init.pending_tasks = {}
            
            init.pending_tasks[task_id] = {
                "user_id": user_id,
                "action": "manual_rename", 
                "final_path": final_path,
                "resource_name": resource_name,
                "selected_path": selected_path,
                "link": link,
                "add2retry": False
            }
            
            # 发送下载成功通知，包含选择按钮
            keyboard = [
                [InlineKeyboardButton("指定标准的TMDB名称", callback_data=f"rename_{task_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # 添加进度信息到成功消息
            progress_text = f" {progress_info}" if progress_info else ""
            message = f"✅{progress_text} 电影\\[`{resource_name}`\\]离线下载完成\\!\n\n便于削刮，请为资源指定TMDB的标准名称！"

            add_task_to_queue(user_id, None, message=message, keyboard=reply_markup)
            
        else:
            # 下载超时，删除任务并提供选择
            init.openapi_115.clear_failed_task(link)
            timeout_message = f"❌ {progress_info} {resource_name} 离线下载超时" if progress_info else f"❌ {resource_name} 离线下载超时"
            init.logger.warn(timeout_message)
            
            # 为失败重试也使用时间戳ID
            retry_task_id = str(int(time.time() * 1000))
            
            # 将重试任务数据存储到全局字典中
            if not hasattr(init, 'pending_tasks'):
                init.pending_tasks = {}
                
            init.pending_tasks[retry_task_id] = {
                "user_id": user_id,
                "action": "retry_download",
                "selected_path": selected_path,
                "resource_name": resource_name,
                "link": link,
                "add2retry": True
            }
            
            # 提供重试选项
            keyboard = [
                [InlineKeyboardButton("指定TMDB名称并添加到重试列表", callback_data=f"rename_{retry_task_id}")],
                [InlineKeyboardButton("取消", callback_data="cancel_download")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # 添加进度信息到失败消息
            failure_text = f"{progress_info} " if progress_info else ""
            message = f"`{link}`\n\n😭 {failure_text}离线下载超时，请选择后续操作："

            add_task_to_queue(user_id, None, message=message, keyboard=reply_markup)
            
    except Exception as e:
        error_msg = f"💀{progress_info} 下载遇到错误: {str(e)}" if progress_info else f"💀下载遇到错误: {str(e)}"
        init.logger.error(error_msg)

        user_error_msg = f"❌ {progress_info} 下载任务执行出错: {str(e)}" if progress_info else f"❌ 下载任务执行出错: {str(e)}"
        add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png", message=user_error_msg)
    finally:
        # ⚠️ 注意：此函数已弃用，使用 clear_cloud_task() 会清空所有离线任务
        # 建议：应该只清理当前任务（通过 del_faild_offline_task + info_hash）
        # 保留此调用以保持向后兼容，但不推荐使用此函数
        init.openapi_115.clear_cloud_task()


async def handle_manual_rename_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理手动重命名的回调"""
    query = update.callback_query
    await query.answer()
    
    try:
        # 从callback_data中提取task_id
        task_id = query.data.replace("rename_", "")
        
        # 从全局存储中获取任务数据
        if hasattr(init, 'pending_tasks') and task_id in init.pending_tasks:
            task_data = init.pending_tasks[task_id]
            
            # 将数据保存到用户上下文中（用于后续的重命名操作）
            context.user_data["rename_data"] = task_data

            await query.edit_message_text(f"`{task_data['resource_name']}`\n\n📝 请直接回复TMDB标准名称进行重命名：\n\\(点击资源名称自动复制\\)", parse_mode='MarkdownV2')

            # 清理已使用的任务数据
            del init.pending_tasks[task_id]
        else:
            await query.edit_message_text("❌ 任务数据已过期，请重新下载")
        
    except Exception as e:
        init.logger.error(f"处理手动重命名回调失败: {e}")
        await query.edit_message_text("❌ 处理失败，请稍后再试")


async def handle_batch_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理批量移动文件命令: 移动到[文件夹名]

    ⚠️ 已废弃：此功能不再使用，保留代码仅供参考
    """
    message_text = update.message.text.strip()
    user_id = update.effective_user.id

    # 检查是否是"移动到"命令
    if not message_text.startswith("移动到"):
        return

    # 提取文件夹名（支持中英文方括号）
    folder_match = re.match(r'移动到[\[\【\［](.+?)[\]\】\］]', message_text)
    if not folder_match:
        await update.message.reply_text("⚠️ 格式错误！请使用: 移动到[文件夹名]")
        return

    target_folder_name = folder_match.group(1).strip()
    if not target_folder_name:
        await update.message.reply_text("⚠️ 文件夹名不能为空！")
        return

    # 清理文件夹名
    target_folder_name = sanitize_folder_name(target_folder_name)

    # 查找最近的批量下载记录
    if not hasattr(init, 'batch_downloads') or not init.batch_downloads:
        await update.message.reply_text("⚠️ 没有找到可移动的批量下载记录！")
        return

    # 找到该用户最近的批量下载
    user_batches = [(batch_id, data) for batch_id, data in init.batch_downloads.items()
                    if data['user_id'] == user_id]

    if not user_batches:
        await update.message.reply_text("⚠️ 没有找到您的批量下载记录！")
        return

    # 使用最新的批量下载记录(按batch_id降序)
    batch_id, batch_data = sorted(user_batches, key=lambda x: x[0], reverse=True)[0]
    download_path = batch_data['download_path']
    resource_names = batch_data['resource_names']

    try:
        # 创建目标文件夹
        init.openapi_115.create_dir_for_file(download_path, target_folder_name)
        target_path = f"{download_path}/{target_folder_name}"
        init.logger.info(f"创建目标文件夹: {target_path}")

        # 批量移动文件/文件夹
        moved_count = 0
        failed_count = 0

        # 去重资源名称列表（避免重复的temp）
        unique_resources = list(set(resource_names))

        for resource_name in unique_resources:
            try:
                source_path = f"{download_path}/{resource_name}"

                # 直接移动文件或文件夹
                init.openapi_115.move_file(source_path, target_path)
                moved_count += 1
                init.logger.info(f"移动: {resource_name} -> {target_path}")

            except Exception as e:
                failed_count += 1
                init.logger.error(f"移动失败 {resource_name}: {e}")

        # 发送结果消息
        result_message = f"✅ 批量移动完成！\n\n"
        result_message += f"📁 目标文件夹: `{target_folder_name}`\n"
        result_message += f"✅ 成功: {moved_count} 个\n"
        if failed_count > 0:
            result_message += f"❌ 失败: {failed_count} 个"

        await update.message.reply_text(result_message, parse_mode='MarkdownV2')

        # 清除已使用的批量下载记录
        del init.batch_downloads[batch_id]

    except Exception as e:
        init.logger.error(f"批量移动失败: {e}")
        await update.message.reply_text(f"❌ 批量移动失败: {str(e)}")


def extract_txt_created_time_str(message) -> str:
    """
    提取 TXT 文件消息的创建时间（UTC+8，格式 YYYYMMDD_HHMM）。
    说明：Telegram Bot API 不透传本地文件系统 ctime，因此优先取转发消息的原始创建时间
    (forward_origin.date)，若非转发则取该文件消息的上传时间 (message.date)。
    """
    from datetime import datetime, timezone, timedelta

    tz_cn = timezone(timedelta(hours=8))
    dt = None
    if message is not None:
        forward_origin = getattr(message, "forward_origin", None)
        if forward_origin is not None:
            dt = getattr(forward_origin, "date", None)
        if dt is None:
            dt = getattr(message, "forward_date", None)
        if dt is None:
            dt = getattr(message, "date", None)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_cn = dt.astimezone(tz_cn)
    else:
        dt_cn = datetime.now(tz_cn)
    return dt_cn.strftime("%Y%m%d_%H%M")


async def handle_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户发送的 txt 文件"""
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return

    document = update.message.document

    # 检查文件类型（兼容大小写 .txt/.TXT）
    if not document or not (document.file_name or "").lower().endswith('.txt'):
        return

    # 检查文件大小（500KB = 512000 bytes）
    if document.file_size > 512000:
        await update.message.reply_text("⚠️ txt 文件大于 500KB，已忽略！")
        await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_ERROR)
        return

    try:
        # 下载文件
        file = await context.bot.get_file(document.file_id)
        file_content_bytes = await file.download_as_bytearray()

        # 尝试多种编码解码文件内容
        file_content = None
        for encoding in ['utf-8', 'gbk', 'gb2312', 'utf-16']:
            try:
                file_content = file_content_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if not file_content:
            await update.message.reply_text("⚠️ 无法解析 txt 文件编码，请检查文件格式！")
            await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_ERROR)
            return

        # 提取文件名（不含扩展名，兼容大小写 .txt/.TXT）
        file_base_name = document.file_name[:-4] if document.file_name.lower().endswith('.txt') else document.file_name

        # 按照粘贴磁力文件的逻辑解析链接（使用统一的链接提取函数）
        lines = file_content.split('\n')
        valid_links = extract_links_from_lines(lines)

        # 检查是否有有效链接
        if not valid_links:
            await update.message.reply_text("⚠️ txt 文件中未找到有效的下载链接！")
            await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_ERROR)
            return

        init.logger.info(f"从 txt 文件 {document.file_name} 中提取到 {len(valid_links)} 个有效链接")

        # 构建自定义文件夹名称
        custom_folder_name = ""

        # 提取内容首行（非链接）- 使用统一的链接检测函数
        first_non_link_line = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 使用统一的链接检测函数（支持带前缀的链接）
            if not line_contains_link(line):
                first_non_link_line = line
                break

        # 组合文件夹名：文件名 + 首行内容（如果存在）+ TXT文件创建时间
        base_folder_name = f"{file_base_name}{first_non_link_line}" if first_non_link_line else file_base_name
        created_time_suffix = extract_txt_created_time_str(update.message)
        custom_folder_name = f"{base_folder_name}_{created_time_suffix}"

        # 清理文件夹名
        custom_folder_name = sanitize_folder_name(custom_folder_name)

        # 保存到 context
        context.user_data["links"] = valid_links
        context.user_data["custom_folder_name"] = custom_folder_name
        context.user_data["total_links"] = len(valid_links)
        context.user_data["reaction_targets"] = [(update.effective_chat.id, update.message.message_id)]

        init.logger.info(f"txt 文件提取的自定义文件夹名: {custom_folder_name}")

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        last_save_path = get_user_last_save_path(user_id)
        if last_save_path:
            auto_note = "🤖 已自动使用上次的保存路径，发送 /reset_save_path 可重新选择。"
            await finalize_download_submission(
                context,
                user_id,
                last_save_path,
                custom_folder_name,
                chat_id=chat_id,
                auto_note=auto_note
            )
            return

        # 构建分类选择键盘
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])]
            for category in init.bot_config['category_folder']
        ]

        keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # 发送选择消息
        link_count = len(valid_links)

        # 获取配额信息
        quota_info_text = ""
        quota_warning = ""
        try:
            quota_info = init.openapi_115.get_quota_info()
            if quota_info:
                total_quota = quota_info.get('count', 0)
                used_quota = quota_info.get('used', 0)
                remaining_quota = total_quota - used_quota
                quota_info_text = f"\n💾 本月配额还剩 {remaining_quota}/{total_quota} 个。即将消耗 {link_count} 个下载配额。"

                if remaining_quota < link_count:
                    quota_warning = "\n⚠️ 配额不足以完成此次任务，可能无法完成所有下载。"
        except Exception as e:
            init.logger.warn(f"获取配额信息失败: {e}")

        # 提取文件夹名（用于显示）
        folder_hint = f"\n📁 即将下载至: 选择的文件夹/[{custom_folder_name}]"

        message_text = f"📄 从 txt 文件检测到 {link_count} 个下载链接{folder_hint}{quota_info_text}{quota_warning}\n\n❓请选择要保存到哪个分类："

        await update.message.reply_text(
            text=message_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        init.logger.error(f"处理 txt 文件失败: {e}", exc_info=True)
        await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_ERROR)
        await update.message.reply_text(f"❌ 处理 txt 文件时出错: {str(e)}")


async def handle_manual_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理手动重命名（通过独立的消息处理器）"""
    # 检查消息是否存在文本内容
    if not update.message or not update.message.text:
        return

    # ⚠️ 已废弃：不再处理"移动到"命令
    # message_text = update.message.text.strip()
    # if message_text.startswith("移动到"):
    #     await handle_batch_move(update, context)
    #     return

    # 检查用户是否有待处理的重命名数据
    message_text = update.message.text.strip()
    rename_data = context.user_data.get("rename_data")
    if not rename_data:
        return
    
    try:
        new_resource_name = update.message.text.strip()
        
        # 获取重命名所需的数据
        old_resource_name = rename_data["resource_name"]
        selected_path = rename_data["selected_path"]
        download_url = rename_data["link"]
        add2retry = rename_data["add2retry"]
        
        # 添加到重试列表
        if add2retry:
            save_failed_download_to_db(
                new_resource_name, 
                download_url, 
                selected_path
            )
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ 已将失败任务添加到重试列表，系统将自动重试！")
            context.user_data.pop("rename_data", None)
            return

        final_path = rename_data["final_path"]
        # 执行重命名
        init.openapi_115.rename(final_path, new_resource_name)
        
        # 构建新的路径
        new_final_path = f"{selected_path}/{new_resource_name}"
        
        # 获取文件列表并创建STRM文件
        file_list = init.openapi_115.get_files_from_dir(new_final_path)
        create_strm_file(new_final_path, file_list)
        
        # 发送封面图片（如果有的话）
        cover_url = ""
        
        # 根据分类获取封面
        cover_url = get_movie_cover(new_resource_name)
        
        # 通知Emby扫库
        is_noticed = notice_emby_scan_library()
        if is_noticed:
            message = f"✅ 重命名成功：`{new_resource_name}`\n\n**👻 已通知Emby扫库，请稍后确认！**"
        else:
            message = f"✅ 重命名成功：`{new_resource_name}`\n\n**⚠️ 未能通知Emby，请先配置'EMBY API KEY'！**"
        if cover_url:
            try:
                init.logger.info(f"cover_url: {cover_url}")
                
                if not init.aria2_client:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=cover_url, 
                        caption=message,
                        parse_mode='MarkdownV2'
                    )
                else:
                    # 推送到aria2
                   await push2aria2(new_final_path, cover_url, message, update, context)
            except TelegramError as e:
                init.logger.warn(f"Telegram API error: {e}")
            except Exception as e:
                init.logger.warn(f"Unexpected error: {e}")
        else:
            if not init.aria2_client:
                await context.bot.send_message(
                                                chat_id=update.effective_chat.id,
                                                text=message,
                                                parse_mode='MarkdownV2'
                )
            else:
                # 推送到aria2
                await push2aria2(new_final_path, cover_url, message, update, context)
        
        # 清除重命名数据，结束当前操作
        context.user_data.pop("rename_data", None)
        init.logger.info(f"重命名操作完成：{old_resource_name} -> {new_resource_name}")
        await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_DONE)
        
    except Exception as e:
        init.logger.error(f"重命名处理失败: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ 重命名失败: {str(e)}"
        )
        await set_message_reaction_safe(context.bot, update.effective_chat.id, update.message.message_id, REACTION_ERROR)
        # 出错时也清除数据，结束当前操作
        context.user_data.pop("rename_data", None)
        
        
async def push2aria2(new_final_path, cover_url, message, update, context):
    
    # 为Aria2推送创建任务ID系统
    import uuid
    push_task_id = str(uuid.uuid4())[:8]
    
    # 初始化pending_push_tasks（如果不存在）
    if not hasattr(init, 'pending_push_tasks'):
        init.pending_push_tasks = {}
    
    # 存储推送任务数据
    init.pending_push_tasks[push_task_id] = {
        'path': new_final_path
    }
    
    device_name = init.bot_config.get('aria2', {}).get('device_name', 'Aria2') or 'Aria2'
    
    keyboard = [
        [InlineKeyboardButton(f"推送到{device_name}", callback_data=f"push2aria2_{push_task_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if cover_url:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id, 
            photo=cover_url, 
            caption=message,
            parse_mode='MarkdownV2',
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode='MarkdownV2',
            reply_markup=reply_markup
        )


def register_download_handlers(application):
    # 允许用户手动重置保存目录
    application.add_handler(CommandHandler("reset_save_path", reset_save_path_command))

    # 命令形式的下载交互（同时支持纯文本、转发图片/视频配文 caption 以及 text_link 实体）
    download_command_handler = ConversationHandler(
        # entry_points=[CommandHandler("dl", start_d_command)],
        entry_points=[
            MessageHandler(
                DOWNLOAD_LINK_FILTER & ~filters.COMMAND,
                start_d_command
            )
        ],
        states={
            SELECT_MAIN_CATEGORY: [CallbackQueryHandler(select_main_category)],
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)]
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_command_handler)

    # 添加 txt 文件处理器（优先级高于默认 group=0，同时兼容 text/plain 与 .txt 扩展名）
    application.add_handler(MessageHandler(
        filters.Document.TXT | filters.Document.FileExtension("txt"),
        handle_txt_file
    ), group=-1)

    # 添加全局分类选择处理器（支持定时器触发的消息）
    # 使用 group=0 确保它与 ConversationHandler 并行处理
    application.add_handler(CallbackQueryHandler(handle_category_selection_global))

    # 添加独立的回调处理器处理异步任务的后续操作
    application.add_handler(CallbackQueryHandler(handle_manual_rename_callback, pattern=r"^rename_"))
    application.add_handler(CallbackQueryHandler(handle_retry_callback, pattern=r"^retry_"))
    application.add_handler(CallbackQueryHandler(handle_download_failure, pattern=r"^cancel_download$"))

    # 添加消息处理器处理重命名输入（使用较低优先级的组别）
    # group=1 表示优先级低于默认的 group=0
    # application.add_handler(MessageHandler(
    #     filters.TEXT & ~filters.COMMAND,
    #     handle_manual_rename
    # ), group=1)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'(magnet:|ed2k://|ED2K://|thunder://)'),
        handle_manual_rename
    ), group=1)
    init.logger.info("✅ Downloader处理器已注册")
