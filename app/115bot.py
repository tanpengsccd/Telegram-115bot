# -*- coding: utf-8 -*-
"""
  _________        .__                          __   
 /   _____/______  |__|_______   ____    ____ _/  |_ 
 \_____  \ \____ \ |  |\_  __ \_/ __ \  /    \\   __\
 /        \|  |_> >|  | |  | \/\  ___/ |   |  \|  |  
/_______  /|   __/ |__| |__|    \___  >|___|  /|__|  
        \/ |__|                     \/      \/          
 * Create by: yu fei
 * Date: 2024/10/25
 * Time: 14:08
 * Name: 
 * Purpose: 
 * Copyright © 2024年 Fei. All rights reserved.
"""
import json
from subscribe import add_task_to_queue, queue_worker
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
import init
import asyncio
import threading
from cookie_handler import register_cookie_handlers
from download_handler import register_download_handlers
from sync_handler import register_sync_handlers
from video_handler import register_video_handlers
from subscribe_handler import register_subscribe_handlers
from scheduler import start_scheduler_in_thread


def get_version(md_format=False):
    if md_format:
        return "v2\.2"
    return "v2.2"

def get_help_info():
    version = get_version()
    help_info = f"""
            <b>115 Bot {version} 使用说明</b>
            <b>命令列表:</b>
            /start       - 显示帮助信息  
            /cookie   - 设置115Cookie  
            /dl       - 添加离线下载
            /sync     - 同步目录，并创建软链
            /sub      - AV女优订阅
            /q        - 取消当前会话  
            
            <b>功能说明:</b>  
            - '/dl 下载链接' 即可添加离线下载，支持磁力、115分享、迅雷、ed2k、FTP、HTTPS等格式。  
            - 当发送视频文件时，机器人会将视频文件保存到115。
            - 同步目录会自动创建strm文件到本地的软链根目录(对应配置文件中的strm_root), 每次同步会清空对应的strm目录。如同步目录下文件较多，耗时会比较长，请耐心等待。
            - 订阅功能目前只支持女优订阅，输入女优名称，当有新作品时自动下载到指定目录并添加软链。
            
            <b>提示：</b>
            在使用115相关功能前，请先设置115Cookie！
        """
    return help_info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_info = get_help_info()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=help_info, parse_mode="html")

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
    version = get_version(md_format=True)
    cookie_health = "115cookie检测正常！"
    for item in init.bot_config['allowed_user_list']:
        if not init.initialize_115client():
            cookie_health = "检测到115Cookie已过期，请重新设置！"
        add_task_to_queue(item, "/app/images/neuter010.png", f"115 Bot {version} 启动成功！\[{cookie_health}\] *发送 `/start` 查看操作说明。*")


if __name__ == '__main__':
    init.init()
    init.logger.info("Starting bot with configuration:")
    init.logger.info(json.dumps(init.bot_config))
    token = init.bot_config['bot_token']
    application = Application.builder().token(token).build()

    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)

    # 注册Cookie
    register_cookie_handlers(application)
    # 注册下载
    register_download_handlers(application)
    # 注册同步
    register_sync_handlers(application)
    # 注册视频
    register_video_handlers(application)
    # 注册订阅
    register_subscribe_handlers(application)

    # 启动机器人轮询
    try:
        # 启动消息队列
        message_thread = threading.Thread(target=start_async_loop, daemon=True)
        message_thread.start()
        # 启动订阅线程
        start_scheduler_in_thread()
        init.logger.info("订阅线程启动成功！")
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
