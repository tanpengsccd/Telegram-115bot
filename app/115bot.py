# -*- coding: utf-8 -*-

import json
import os
import time
import asyncio
import threading
from telegram import Update, BotCommand
from telegram.ext import ContextTypes, CommandHandler, Application, TypeHandler
from telegram.request import HTTPXRequest
from telegram.helpers import escape_markdown

# 导入init模块（此时__init__.py已经设置了模块路径）
import init

from app.utils.message_queue import add_task_to_queue, queue_worker
from app.utils.reaction import REACTION_PROCESSING, REACTION_ERROR, set_message_reaction_safe
from app.handlers.menu_handler import (
    register_menu_handlers,
    register_fallback_handlers,
    build_help_text,
    send_panel_result,
    refresh_bottom_panel_if_exists,
)
from app.handlers.auth_handler import register_auth_handlers
from app.handlers.download_handler import register_download_handlers
from app.handlers.sync_handler import register_sync_handlers
from app.handlers.video_handler import register_video_handlers
from app.core.scheduler import start_scheduler_in_thread
from app.handlers.av_download_handler import register_av_download_handlers
from app.handlers.offline_task_handler import register_offline_task_handlers
from app.handlers.aria2_handler import register_aria2_handlers


def get_version(md_format=False):
    version = "v3.2.13"
    if md_format:
        return escape_markdown(version, version=2)
    return version


def get_help_info():
    return build_help_text()

def start_async_loop():
    """启动异步事件循环的线程"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init.logger.info("事件循环已启动")
    try:
        token = init.bot_config['bot_token']
        loop.create_task(queue_worker(loop, token))
        loop.run_forever()
    except Exception as e:
        init.logger.error(f"事件循环异常: {e}")
    finally:
        loop.close()
        init.logger.info("事件循环已关闭")

def send_start_message():
    version = get_version()  
    if init.openapi_115 is None:
        return
    
    line1, line2, line3, line4 = init.openapi_115.welcome_message()
    if not line1:
        return
    line5 = escape_markdown(f"Telegram-115Bot {version} 启动成功！", version=2)
    if line1 and line2 and line3 and line4:
        formatted_message = f"""
{line1}
{line2}
{line3}
{line4}

{line5}

发送 `/start` 查看操作说明"""
        
        add_task_to_queue(
            init.get_primary_user(),
            f"{init.IMAGE_PATH}/neuter010.png",
            message=formatted_message
        )


def update_logger_level():
    import logging
    # 禁用 HTTP 库的冗余日志
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
    # 禁用 Telegram 库的冗余日志
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Application').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Updater').setLevel(logging.WARNING)
    logging.getLogger('telegram.Bot').setLevel(logging.WARNING)
    
def get_bot_menu():
    return [
        BotCommand("start", "打开主控面板"),
        BotCommand("status", "查看账号、配额、容量与目录"),
        BotCommand("tasks", "查看任务队列与磁力明细"),
        BotCommand("cloud_tasks", "管理115官方云端磁力任务"),
        BotCommand("log", "获取机器人运行日志文件"),
        BotCommand("minimal", "切换对话极简模式"),
        BotCommand("help", "完整使用说明"),
        BotCommand("rl", "查看重试列表"),
        BotCommand("reset_save_path", "重置记住的默认保存目录"),
        BotCommand("auth", "115扫码授权"),
        BotCommand("reload", "重载配置"),
        BotCommand("av", "指定番号下载"),
        BotCommand("sync", "同步指定目录，并创建软链"),
        BotCommand("q", "退出当前会话"),
    ]
    

async def set_bot_menu(application):
    """异步设置Bot菜单"""
    try:
        await application.bot.set_my_commands(get_bot_menu())
        init.logger.info("Bot菜单命令已设置!")
    except Exception as e:
        init.logger.error(f"设置Bot菜单失败: {e}")

async def post_init(application):
    """应用初始化后的回调"""
    await set_bot_menu(application)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理器：记录堆栈并向触发用户反馈失败原因，避免静默无响应"""
    import html
    import traceback
    error_details = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    init.logger.error(f"处理更新时发生异常: {context.error}\n{error_details}")

    try:
        if update and getattr(update, "effective_chat", None) and getattr(update, "effective_user", None):
            if init.check_user(update.effective_user.id):
                chat_id = update.effective_chat.id
                err_text = html.escape(str(context.error)[:300])
                await send_panel_result(context.bot, chat_id, f"❌ 执行失败：<code>{err_text}</code>")
                await refresh_bottom_panel_if_exists(context.bot, chat_id)
                if getattr(update, "message", None):
                    await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_ERROR)
    except Exception as notify_err:
        init.logger.debug(f"发送异常反馈消息失败: {notify_err}")


async def log_raw_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """最先执行的原始更新日志，用于确认Telegram是否把消息/按钮回调投递给了机器人"""
    if update.callback_query:
        init.logger.info(
            f"[RAW UPDATE] callback_query: data={update.callback_query.data!r}, "
            f"chat_id={update.effective_chat.id if update.effective_chat else None}, "
            f"user_id={update.effective_user.id if update.effective_user else None}"
        )
    elif update.message:
        msg = update.message
        init.logger.info(
            f"[RAW UPDATE] message: msg_id={msg.message_id}, "
            f"user_id={update.effective_user.id if update.effective_user else None}, "
            f"text_len={len(msg.text) if msg.text else 0}, "
            f"caption_len={len(msg.caption) if msg.caption else 0}, "
            f"has_doc={bool(msg.document)}, has_photo={bool(msg.photo)}, has_video={bool(msg.video)}, "
            f"forwarded={bool(msg.forward_origin)}"
        )


async def react_on_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """收到授权用户发送的文本消息或文件时，立即添加 👀 表情回应表示已收到正在处理"""
    if not update.message or not update.effective_user:
        return
    if not init.check_user(update.effective_user.id):
        return
    await set_message_reaction_safe(
        context.bot,
        update.effective_chat.id,
        update.message.message_id,
        REACTION_PROCESSING,
    )


def build_bot_request():
    """构造Bot API请求客户端；proxy仅在配置了HTTP_PROXY/HTTPS_PROXY时生效，否则直连"""
    import httpx
    proxy = (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip() or None
    return HTTPXRequest(
        connection_pool_size=8,
        proxy=proxy,
        connect_timeout=10,
        read_timeout=20,
        write_timeout=20,
        pool_timeout=10,
        # 主动回收长时间空闲的keep-alive连接，避免复用到失效连接而报502 Bad Gateway
        httpx_kwargs={"limits": httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=15.0)},
    )


if __name__ == '__main__':
    init.init()
    # 启动消息队列
    message_thread = threading.Thread(target=start_async_loop, daemon=True)
    message_thread.start()
    # 等待消息队列准备就绪
    import app.utils.message_queue as message_queue
    max_wait = 30  # 最多等待30秒
    wait_count = 0
    while True:
        if message_queue.global_loop is not None:
            init.logger.info("消息队列线程已准备就绪！")
            break
        time.sleep(1)
        wait_count += 1
        if wait_count >= max_wait:
            init.logger.error("消息队列线程未准备就绪，程序将退出。")
            exit(1)
    init.logger.info("Starting bot with configuration:")
    init.logger.info(json.dumps(init.bot_config))
    # 调整telegram日志级别
    update_logger_level()
    token = init.bot_config['bot_token']
    application = (
        Application.builder()
        .token(token)
        .request(build_bot_request())
        .get_updates_request(build_bot_request())
        .concurrent_updates(4)  # 避免单个耗时任务阻塞其它按钮/命令响应
        .post_init(post_init)
        .build()
    )
    application.add_error_handler(on_error)
    # 使用独立的低优先级编号组(-100 / -99)，避免吞掉 group=-1 中的 TXT 文件等业务处理器
    application.add_handler(TypeHandler(Update, log_raw_update), group=-100)
    application.add_handler(TypeHandler(Update, react_on_incoming_message), group=-99)

    # 注册主控面板、状态、任务、帮助与重载命令（必须在 download_handler 全局回调之前注册）
    register_menu_handlers(application)
    
    # 初始化115open对象
    is_115_ready = init.initialize_115open()
    if not is_115_ready:
        if init.openapi_115 is None:
            init.logger.error("115 OpenAPI客户端初始化失败，程序无法继续运行！")
            add_task_to_queue(
                init.get_primary_user(),
                f"{init.IMAGE_PATH}/male023.png",
                message=escape_markdown("❌ 115 OpenAPI客户端初始化失败，程序无法继续运行！\n请检查Token或115 AppID设置是否正确！", version=2)
            )
            # 等待消息队列处理完毕再退出
            while not message_queue.message_queue.empty():
                time.sleep(5)
            time.sleep(30)
            exit(1)
        else:
            init.logger.warn("115 Token 无效或已过期，机器人将继续启动以便使用 /auth 重新扫码授权。")
            add_task_to_queue(
                init.get_primary_user(),
                f"{init.IMAGE_PATH}/male023.png",
                message=escape_markdown("⚠️ 115 授权已过期或无效！\n机器人已启动，请发送 /auth 重新扫码授权。", version=2)
            )


    # 注册Auth
    register_auth_handlers(application)
    # 注册下载
    register_download_handlers(application)
    # 注册AV下载
    register_av_download_handlers(application)
    # 注册离线任务
    register_offline_task_handlers(application)
    # 注册Aria2
    register_aria2_handlers(application)
    # 注册同步
    register_sync_handlers(application)
    # 注册视频
    register_video_handlers(application)
    # 最后注册全局兜底处理器，确保任何无法识别的消息或文件都有明确反馈
    register_fallback_handlers(application)

    # 启动机器人轮询
    try:
        # 启动定时任务线程
        start_scheduler_in_thread()
        init.logger.info("定时任务线程启动成功！")
        # 同进程启动 Web UI & REST API 服务（当 web.enabled=true 时）
        try:
            from app.web.app import start_web_server_in_thread
            start_web_server_in_thread()
        except Exception as web_err:
            init.logger.error(f"启动 Web/API 服务失败（不影响 TG Bot 运行）: {web_err}")
        time.sleep(3)  # 等待定时任务线程启动
        if is_115_ready:
            send_start_message()
        # 显式声明接收所有更新类型，避免callback_query等更新被服务端静默丢弃
        application.run_polling(allowed_updates=Update.ALL_TYPES)  # 阻塞运行
    except KeyboardInterrupt:
        init.logger.info("程序已被用户终止（Ctrl+C）。")
    except SystemExit:
        init.logger.info("程序正在退出。")
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()  # 获取完整的异常堆栈信息
        init.logger.error(f"程序遇到错误：{str(e)}\n{error_details}")
    finally:
        init.logger.info("机器人已停止运行。")