# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError
import init
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

    # 过滤掉空行和只包含链接的行
    non_link_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 先检查整行是否是链接（支持包含空格的ED2K链接）
        if is_valid_link(line) != DownloadUrlType.UNKNOWN:
            # 整行是链接，跳过
            continue

        # 整行不是链接，检查行内是否包含链接（按空格分割检查）
        contains_link = False
        for fragment in line.split():
            if is_valid_link(fragment.strip()) != DownloadUrlType.UNKNOWN:
                contains_link = True
                break

        # 如果该行不包含链接，直接使用
        if not contains_link:
            non_link_lines.append(line)
        else:
            # 提取该行中的非链接文字部分
            text_parts = []
            for fragment in line.split():
                fragment = fragment.strip()
                if fragment and is_valid_link(fragment) == DownloadUrlType.UNKNOWN:
                    text_parts.append(fragment)
            if text_parts:
                non_link_lines.append(' '.join(text_parts))

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

        if custom_folder_name:
            init.logger.info(f"从合并消息中提取到自定义文件夹名: {custom_folder_name}")

        # 构建分类选择键盘
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])]
            for category in init.bot_config['category_folder']
        ]

        # 只在有最后保存路径时才显示该选项
        if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
            last_save_path = init.bot_session['movie_last_save']
            keyboard.append([InlineKeyboardButton(f"📁 上次保存: {last_save_path}", callback_data="last_save_path")])

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


async def start_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return ConversationHandler.END

    message_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # 解析多个下载链接（按行分割，支持包含空格的链接）
    # 先尝试按行提取完整链接（支持ED2K等包含空格的文件名）
    lines = message_text.split('\n')
    valid_links = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检查整行是否是有效链接
        dl_url_type = is_valid_link(line)
        if dl_url_type != DownloadUrlType.UNKNOWN:
            valid_links.append({"link": line, "type": dl_url_type})
            continue

        # 如果整行不是链接，尝试按空格分割（兼容多个链接在同一行的情况）
        fragments = line.split()
        for fragment in fragments:
            fragment = fragment.strip()
            if not fragment:
                continue
            dl_url_type = is_valid_link(fragment)
            if dl_url_type != DownloadUrlType.UNKNOWN:
                valid_links.append({"link": fragment, "type": dl_url_type})

    # 检查是否有有效链接
    if not valid_links:
        await update.message.reply_text("⚠️ 未找到有效的下载链接，请检查格式后重试！")
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
        "timer_task": None,
        "context": context,
        "chat_id": chat_id
    })

    # 添加消息文本和链接到缓冲区
    buffer["messages"].append(message_text)
    buffer["links"].extend(valid_links)
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
        if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
            last_save_path = init.bot_session["movie_last_save"]
            links = context.user_data["links"]
            user_id = update.effective_user.id
            total_count = len(links)

            # 获取自定义文件夹名(如果有)
            custom_folder_name = context.user_data.get("custom_folder_name", "")

            await query.edit_message_text(f"✅ 已为您添加 {total_count} 个下载任务到队列！\n请稍后~")

            # 使用批量下载处理（减少API调用频率）
            download_executor.submit(download_tasks_batch, links, last_save_path, user_id, custom_folder_name)
            return ConversationHandler.END
        else:
            await query.edit_message_text("❌ 未找到最后一次保存路径，请重新选择分类")
            return ConversationHandler.END
    else:
        context.user_data["selected_main_category"] = query_data
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == query_data
        ][0]

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
    # 保存最后一次选择路径
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    init.bot_session['movie_last_save'] = selected_path

    if selected_path == "cancel":
        return await quit_conversation(update, context)
    links = context.user_data["links"]
    selected_main_category = context.user_data["selected_main_category"]
    user_id = update.effective_user.id
    total_count = len(links)

    # 获取自定义文件夹名(如果有)
    custom_folder_name = context.user_data.get("custom_folder_name", "")

    await query.edit_message_text(f"✅ 已为您添加 {total_count} 个下载任务到队列！\n请稍后~")

    # 使用批量下载处理（减少API调用频率）
    download_executor.submit(download_tasks_batch, links, selected_path, user_id, custom_folder_name)
    return ConversationHandler.END


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
    total_count = len(links)

    # 处理取消
    if query_data == "cancel":
        await query.edit_message_text("🚪 已取消下载任务")
        # 清理用户数据
        context.user_data.clear()
        return

    # 处理"上次保存路径"
    if query_data == "last_save_path":
        if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
            last_save_path = init.bot_session["movie_last_save"]
            await query.edit_message_text(f"✅ 已为您添加 {total_count} 个下载任务到队列！\n请稍后~")
            download_executor.submit(download_tasks_batch, links, last_save_path, user_id, custom_folder_name)
            # 清理用户数据
            context.user_data.clear()
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

    # 保存最后一次选择路径
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    init.bot_session['movie_last_save'] = selected_path

    await query.edit_message_text(f"✅ 已为您添加 {total_count} 个下载任务到队列！\n请稍后~")

    # 使用批量下载处理
    download_executor.submit(download_tasks_batch, links, selected_path, user_id, custom_folder_name)

    # 清理用户数据
    context.user_data.clear()


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


def save_failed_download_to_db(title, magnet, save_path):
    """保存失败的下载任务到数据库"""
    try:
        with SqlLiteLib() as sqlite:
            # 检查是否已存在相同的任务
            check_sql = "SELECT * FROM offline_task WHERE magnet = ? AND save_path = ? AND title = ?"
            existing = sqlite.query_one(check_sql, (magnet, save_path, title))

            if not existing:
                sql = "INSERT INTO offline_task (title, magnet, save_path) VALUES (?, ?, ?)"
                sqlite.execute_sql(sql, (title, magnet, save_path))
                init.logger.info(f"[{title}]已添加到重试列表")
    except Exception as e:
        raise str(e)


def format_batch_progress_message(total_count, task_results, max_lines=48):
    """
    格式化批量下载进度消息，限制行数并优先显示错误

    Args:
        total_count: 总任务数
        task_results: 任务结果列表 [{"index": 1, "status": "success/failed", "name": "...", "link": "..."}]
        max_lines: 最大行数（默认48行）

    Returns:
        格式化的消息文本
    """
    # 分离成功和失败的任务
    failed_tasks = [t for t in task_results if t.get("status") == "failed"]
    success_tasks = [t for t in task_results if t.get("status") == "success"]

    # 构建消息头
    message_lines = [f"正在进行的总 {total_count} 个链接的下载任务："]

    # 计算可用行数（减去头部和可能的省略提示）
    available_lines = max_lines - 1  # 头部占1行

    # 优先显示错误信息
    displayed_failed = []
    displayed_success = []
    omitted_failed_count = 0

    # 先添加失败的任务（全部显示）
    for task in failed_tasks:
        display_link = task.get("link", "")[:50] + "..." if len(task.get("link", "")) > 50 else task.get("link", "")
        line = f"❌ {task['index']}/{total_count} [{display_link}] 离线下载超时，已自动添加到重试列表"
        displayed_failed.append(line)

    # 计算剩余可显示成功任务的行数
    remaining_lines = available_lines - len(displayed_failed)

    # 如果还有空间，显示最新的成功任务
    if remaining_lines > 0:
        # 倒序显示最新的成功任务
        success_tasks_to_show = success_tasks[-remaining_lines:]
        for task in success_tasks_to_show:
            line = f"✅ {task['index']}/{total_count} [{task.get('name', '未知')}]"
            displayed_success.append(line)

        # 计算被省略的成功任务数
        omitted_success_count = len(success_tasks) - len(success_tasks_to_show)
    else:
        omitted_success_count = len(success_tasks)

    # 如果失败任务过多，也需要省略一部分
    if len(displayed_failed) > available_lines - 2:  # 保留至少2行给成功任务或省略提示
        # 显示最新的失败任务
        kept_failed = displayed_failed[-(available_lines - 2):]
        omitted_failed_count = len(displayed_failed) - len(kept_failed)
        displayed_failed = kept_failed
        displayed_success = []  # 失败太多，不显示成功的
        omitted_success_count = len(success_tasks)

    # 组装最终消息
    if omitted_failed_count > 0:
        message_lines.append(f"...此处省略 {omitted_failed_count} 条失败详情...")

    message_lines.extend(displayed_failed)

    if omitted_success_count > 0 and displayed_success:
        message_lines.append(f"...此处省略 {omitted_success_count} 条成功详情...")

    message_lines.extend(displayed_success)

    return "\n".join(message_lines)


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
    title = resource_name if resource_name else f"未知资源_{task_index}"
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
        title = resource_name if resource_name else f"未知资源_{task_index}"
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


def download_tasks_batch(links, selected_path, user_id, custom_folder_name=""):
    """批量下载任务处理（减少API调用频率）"""
    from app.utils.message_queue import add_task_to_queue

    total_count = len(links)
    init.logger.info(f"开始批量处理 {total_count} 个下载任务")

    try:
        _download_tasks_batch_impl(links, selected_path, user_id, custom_folder_name)
    except Exception as e:
        init.logger.error(f"批量下载任务执行失败: {e}", exc_info=True)
        add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png",
                         message=f"❌ 批量下载任务执行失败: {str(e)}\n请查看日志获取详细信息")


def _download_tasks_batch_impl(links, selected_path, user_id, custom_folder_name=""):
    """批量下载任务的实际实现（内部函数）"""
    from app.utils.message_queue import add_task_to_queue

    total_count = len(links)
    init.logger.info(f"开始执行批量下载任务实现，共 {total_count} 个任务")

    # 如果有自定义文件夹名且链接数>1,创建自定义文件夹
    actual_download_path = selected_path
    if custom_folder_name and total_count > 1:
        # 在选择的路径下创建自定义文件夹
        try:
            init.openapi_115.create_dir_for_file(selected_path, custom_folder_name)
            actual_download_path = f"{selected_path}/{custom_folder_name}"
            init.logger.info(f"创建自定义文件夹: {actual_download_path}")
        except Exception as e:
            init.logger.error(f"创建自定义文件夹失败: {e}")
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

    # 第二步：初始等待10秒
    initial_wait = 10
    init.logger.info(f"已提交 {len(submitted_tasks)} 个任务，等待 {initial_wait} 秒后开始检查状态...")
    time.sleep(initial_wait)

    # 第三步：轮询检查任务状态，间隔递增（10s, 20s, 30s, 40s, 50s, 60s, 60s...）
    poll_interval = 10  # 起始间隔10秒
    max_interval = 60   # 最大间隔60秒
    max_total_time = 3600  # 最长总等待时间1小时
    total_elapsed = initial_wait
    poll_round = 0
    all_completed = False
    offline_task_status = None

    while total_elapsed < max_total_time:
        poll_round += 1
        init.logger.info(f"第 {poll_round} 次检查任务状态（已等待 {total_elapsed}秒）...")

        try:
            start_time = time.time()
            offline_task_status = init.openapi_115.get_offline_tasks()
            elapsed = time.time() - start_time
            init.logger.info(f"获取任务列表耗时: {elapsed:.2f}秒，返回 {len(offline_task_status) if offline_task_status else 0} 个任务")
        except Exception as e:
            init.logger.error(f"获取离线任务状态时出错: {e}")
            offline_task_status = None

        if not offline_task_status:
            init.logger.error("无法获取离线任务状态")
            if poll_round == 1:  # 第一次就失败才报错
                add_task_to_queue(user_id, f"{init.IMAGE_PATH}/male023.png",
                                 message="❌ 无法获取离线任务状态，请稍后手动检查")
                return
            else:
                # 后续轮询失败，使用上一次的结果
                break

        # 检查所有提交的任务是否都已完成
        init.logger.info(f"开始检查 {len(submitted_tasks)} 个提交任务的状态...")
        completed_count = 0
        processing_count = 0
        for task_info in submitted_tasks:
            if not task_info["submitted"]:
                continue

            link = task_info["link"]
            task_found = False
            for task in offline_task_status:
                if task.get('url') == link:
                    task_found = True
                    task_status = task.get('status')
                    task_percent = task.get('percentDone')
                    task_name = task.get('name', 'unknown')

                    # status=2表示完成，status=1表示下载中，status=-1表示失败
                    # 关键修复：percentDone=100时也视为完成（即使status还是1）
                    if task_percent == 100:
                        completed_count += 1
                    elif task_status == -1:
                        completed_count += 1  # 失败也算完成
                    else:
                        processing_count += 1
                        # 只记录还在下载中的任务（减少日志）
                        if poll_round == 1 or processing_count <= 5:  # 第一轮或前5个
                            init.logger.info(f"[下载中] {task_name}: {task_percent}%")
                    break

            # 如果在列表中没找到，可能已经被清理或失败了
            if not task_found:
                init.logger.warn(f"任务未找到（可能已被清理）: {link[:50]}...")
                completed_count += 1

        submitted_count = len([t for t in submitted_tasks if t['submitted']])
        init.logger.info(f"进度: 已完成 {completed_count}/{submitted_count}, 下载中 {processing_count}/{submitted_count}")

        # 如果所有任务都已完成或失败，退出轮询
        if completed_count >= submitted_count:
            all_completed = True
            init.logger.info("所有任务已完成，停止轮询")
            break

        # 计算下一次检查的等待时间（递增10秒，最大60秒）
        current_wait = min(poll_interval * poll_round, max_interval)

        # 检查是否会超过最大总时间
        if total_elapsed + current_wait >= max_total_time:
            init.logger.warn(f"即将达到最大等待时间（{max_total_time}秒），进行最后一次检查")
            break

        init.logger.info(f"还有 {submitted_count - completed_count} 个任务未完成，等待 {current_wait} 秒后继续检查...")
        time.sleep(current_wait)
        total_elapsed += current_wait

    init.logger.info(f"轮询结束，all_completed={all_completed}, total_elapsed={total_elapsed}秒")

    if not all_completed:
        init.logger.warn(f"达到最大等待时间({total_elapsed}秒)，仍有任务未完成，将处理当前状态的任务")

    # 使用最后一次获取的任务状态
    if not offline_task_status:
        init.logger.error("无任务状态可用，退出处理")
        return

    # 第四步：批量处理每个任务的结果
    successful_downloads = []  # 记录成功下载的资源
    task_results = []  # 记录所有任务结果用于进度显示
    progress_message_id = None  # 进度消息的message_id

    # 对于批量下载(>=2个任务)，发送一个初始进度消息
    if total_count >= 2:
        try:
            import asyncio
            from telegram import Bot

            # 创建同步Bot实例（在线程中使用）
            bot = Bot(token=init.get_bot_token())
            initial_message = format_batch_progress_message(total_count, [])

            # 使用同步方式发送消息（因为在ThreadPoolExecutor线程中）
            import nest_asyncio
            nest_asyncio.apply()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            sent_message = loop.run_until_complete(bot.send_message(chat_id=user_id, text=initial_message))
            progress_message_id = sent_message.message_id
            loop.close()

            init.logger.info(f"已创建批量下载进度消息，message_id: {progress_message_id}")
        except Exception as e:
            init.logger.error(f"创建进度消息失败: {e}")

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
        else:
            # 失败的任务直接添加到重试列表
            title = resource_name if resource_name else f"未知资源_{task_index}"
            save_failed_download_to_db(title, link, actual_download_path)
            init.openapi_115.clear_failed_task(link)
            init.logger.info(f"{progress_info} 已添加到重试列表")

            # 记录失败结果
            task_results.append({
                "index": task_index,
                "status": "failed",
                "name": resource_name,
                "link": link
            })

        # 更新进度消息（每10个任务更新一次，或最后一个任务）
        if total_count >= 2 and progress_message_id and (len(task_results) % 10 == 0 or len(task_results) == len(submitted_tasks)):
            try:
                import asyncio
                from telegram import Bot
                bot = Bot(token=init.get_bot_token())
                updated_message = format_batch_progress_message(total_count, task_results)

                # 使用同步方式更新消息
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(bot.edit_message_text(
                    chat_id=user_id,
                    message_id=progress_message_id,
                    text=updated_message
                ))
                loop.close()

                init.logger.info(f"已更新进度消息 ({len(task_results)}/{len(submitted_tasks)})")
            except Exception as e:
                init.logger.warn(f"更新进度消息失败: {e}")

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

    # 第五步：清除云端任务（批量清理一次）
    init.openapi_115.clear_cloud_task()
    init.logger.info(f"批量下载任务处理完成，共 {total_count} 个任务")

    # 批量下载完成后，统一通知 Emby 扫库一次（而不是每个文件都通知）
    if len(final_resource_names) > 0:
        notice_emby_scan_library()
        init.logger.info("已通知 Emby 扫库（批量下载完成）")

    # 第六步：如果有多个成功下载,提示"移动到"功能（无论是否已有自定义文件夹）
    if total_count > 1 and len(final_resource_names) > 0:
        # 保存批量下载信息,用于后续移动操作
        batch_id = str(int(time.time() * 1000))
        if not hasattr(init, 'batch_downloads'):
            init.batch_downloads = {}

        init.batch_downloads[batch_id] = {
            "download_path": actual_download_path,
            "resource_names": final_resource_names,
            "user_id": user_id
        }

        # 提示用户可以使用"移动到"功能
        if custom_folder_name:
            # 如果已经有自定义文件夹，提示可以进一步移动
            message = f"✅ 批量下载完成！共 {len(final_resource_names)}/{total_count} 个任务成功\n\n📁 已保存到: `{custom_folder_name}`\n\n💡 如需进一步移动文件，请回复：\n`移动到[新文件夹名]`"
        else:
            # 如果没有自定义文件夹，提示可以移动
            message = f"✅ 批量下载完成！共 {len(final_resource_names)}/{total_count} 个任务成功\n\n💡 如需批量移动文件，请回复：\n`移动到[自定义文件夹名]`"
        add_task_to_queue(user_id, None, message=message)


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
        # 清除云端任务，避免重复下载
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
    """处理批量移动文件命令: 移动到[文件夹名]"""
    message_text = update.message.text.strip()
    user_id = update.effective_user.id

    # 检查是否是"移动到"命令
    if not message_text.startswith("移动到"):
        return

    # 提取文件夹名
    folder_match = re.match(r'移动到\[(.+?)\]', message_text)
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


async def handle_manual_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理手动重命名（通过独立的消息处理器）"""
    # 先检查是否是"移动到"命令
    message_text = update.message.text.strip()
    if message_text.startswith("移动到"):
        await handle_batch_move(update, context)
        return

    # 检查用户是否有待处理的重命名数据
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
        
        # 检查是否为订阅内容
        from app.core.subscribe_movie import is_subscribe, update_subscribe
        if is_subscribe(new_resource_name):
            # 更新订阅信息
            update_subscribe(new_resource_name, cover_url, download_url)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"💡订阅影片`{new_resource_name}`已手动下载成功\\！",
                parse_mode='MarkdownV2'
            )
        
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
        
    except Exception as e:
        init.logger.error(f"重命名处理失败: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ 重命名失败: {str(e)}"
        )
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
    # 命令形式的下载交互
    download_command_handler = ConversationHandler(
        # entry_points=[CommandHandler("dl", start_d_command)],
         entry_points=[
            MessageHandler(
                filters.TEXT & filters.Regex(r'(magnet:|ed2k://|ED2K://|thunder://)'),
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