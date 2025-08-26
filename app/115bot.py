# -*- coding: utf-8 -*-

import json
from message_queue import add_task_to_queue, queue_worker
from telegram import Update, BotCommand
from telegram.ext import ContextTypes, CommandHandler, Application
import init
import time
import asyncio
import threading
from auth_handler import register_auth_handlers
from download_handler import register_download_handlers
from sync_handler import register_sync_handlers
from video_handler import register_video_handlers
from scheduler import start_scheduler_in_thread
from subscribe_movie_handler import register_subscribe_movie_handlers
from av_download_handler import register_av_download_handlers
from offline_task_handler import register_offline_task_handlers


def get_version(md_format=False):
    if md_format:
        return r"v3\.1\.0"
    return "v3.1.0"

def get_help_info():
    version = get_version()
    help_info = f"""
<b>🍿 Telegram-115Bot {version} 使用手册</b>\n\n
<b>🔧 命令列表</b>\n
<code>/start</code> - 显示帮助信息\n
<code>/auth</code> - <i>115扫码授权 (首次使用必选)</i>\n
<code>/dl</code> - 添加离线下载 [磁力|ed2k|https]\n
<code>/rl</code> - 查看重试列表\n
<code>/av</code> - <i>下载番号资源 (自动匹配磁力)</i>\n
<code>/sm</code> - 订阅电影\n
<code>/sync</code> - 同步目录并创建软链\n
<code>/q</code> - 取消当前会话\n\n
<b>✨ 功能说明</b>\n
<u>离线下载：</u>\n
• 输入 <code>"/dl 下载链接"</code>\n
• 支持磁力/迅雷/ed2k/https\n
• 离线超时可选择添加到重试列表\n
• 根据配置自动生成 <code>.strm</code> 软链文件\n\n
<u>重试列表：</u>\n
• 输入 <code>"/rl"</code>
• 查看当前重试列表，可根据需要选择是否清空\n\n
<u>AV资源：</u>\n
• 输入 <code>"/av 番号"</code>
• 自动检索磁力并离线,默认不生成软链（建议使用削刮工具生成软链）\n\n
<u>电影订阅：</u>\n
• 输入 <code>"/sm 电影名称"</code>
• 自动监控资源更新, 发现更新后自动下载\n\n
<u>目录同步：</u>\n
• 输入 <code>"/sync"</code>\n
• 选择目录后会在对应的目录创建strm软链\n\n
<u>视频下载：</u>\n
• 直接转发视频给机器人，选择保存目录即可保存到115\n
"""
    return help_info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_info = get_help_info()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=help_info, parse_mode="html", disable_web_page_preview=True)

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
    welcome_text = init.openapi_115.welcome_message()
    if welcome_text:
        formatted_message = f"""
`{welcome_text}`

`Telegram-115Bot {version} 启动成功！`

发送 `/start` 查看操作说明"""
        
        add_task_to_queue(
            init.bot_config['allowed_user'], 
            f"{init.IMAGE_PATH}/neuter010.png", 
            message=formatted_message
        )


def update_logger_level():
    import logging
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Application').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Updater').setLevel(logging.WARNING)
    logging.getLogger('telegram.Bot').setLevel(logging.WARNING)
    
def get_bot_menu():
    return  [
        BotCommand("start", "获取帮助信息"),
        BotCommand("auth", "115扫码授权"),
        BotCommand("dl", "添加离线下载"),
        BotCommand("rl", "查看重试列表"),
        BotCommand("av", "指定番号下载"),
        BotCommand("sm", "订阅电影"),
        BotCommand("sync", "同步指定目录，并创建软链"),
        BotCommand("q", "退出当前会话")]
    

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


if __name__ == '__main__':
    init.init()
    # 启动消息队列
    message_thread = threading.Thread(target=start_async_loop, daemon=True)
    message_thread.start()
    # 等待消息队列准备就绪
    import message_queue
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
    application = Application.builder().token(token).post_init(post_init).build()    

    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)


    # 注册Auth
    register_auth_handlers(application)
    # 注册下载
    register_download_handlers(application)
    # 注册离线任务
    register_offline_task_handlers(application)
    # 注册同步
    register_sync_handlers(application)
    # 注册视频
    register_video_handlers(application)
    # 注册AV下载
    register_av_download_handlers(application)
    # 注册电影订阅
    register_subscribe_movie_handlers(application)

    # 启动机器人轮询
    try:
        # 启动订阅线程
        start_scheduler_in_thread()
        init.logger.info("订阅线程启动成功！")
        time.sleep(3)  # 等待订阅线程启动
        send_start_message()
        application.run_polling()  # 阻塞运行
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
