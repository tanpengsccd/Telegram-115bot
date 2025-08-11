# -*- coding: utf-8 -*-

import asyncio
import init
from telegram import Bot

# 全局消息队列
message_queue = asyncio.Queue()
# 全局变量，用于存储事件循环
global_loop = None


def add_task_to_queue(sub_user, post_url, message):
    """向消息队列中添加任务（线程安全）"""
    global global_loop
    if global_loop is None:
        init.logger.error("事件循环尚未启动，无法添加任务到队列")
        return False
    
    try:
        future = asyncio.run_coroutine_threadsafe(
            message_queue.put((sub_user, post_url, message)),
            global_loop 
        )
        future.result(timeout=10)  # 等待任务添加到队列，设置超时时间
        init.logger.info(f"任务已添加到队列: {sub_user}, {post_url}, {message}")
        return True
    except TimeoutError:
        init.logger.error(f"添加任务到队列超时: {sub_user}, {post_url}, {message}")
        return False
    except Exception as e:
        init.logger.error(f"添加任务到队列失败: {e}")
        return False
        
        
async def queue_worker(loop, token):
    global global_loop
    """ 后台队列处理任务 """
    global_loop = loop
    # bot
    bot = Bot(token=token)
    init.logger.info("消息队列线程启动成功！")
    while True:
        try:
            # 从队列获取任务
            sub_user, post_url, message = await message_queue.get()
            init.logger.info(f"取出任务: 用户[{sub_user}], 链接[{post_url}], 消息[{message}]")
            # 执行发送
            await bot.send_photo(
                chat_id=sub_user,
                photo=post_url,
                caption=message,
                parse_mode="MarkdownV2"
            )
            init.logger.info(f"消息已发送至 {sub_user}")
            # 标记任务完成
            message_queue.task_done()
            # 间隔防止速率限制
            await asyncio.sleep(3)
        except Exception as e:
            init.logger.error(f"队列任务处理失败: {e}")
        