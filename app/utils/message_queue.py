# -*- coding: utf-8 -*-

import asyncio
import init
from telegram import Bot

# 全局消息队列
message_queue = asyncio.Queue()
# 全局变量，用于存储事件循环
global_loop = None


def add_task_to_queue(sub_user, post_url, message, keyboard=None):
    """向消息队列中添加任务（线程安全）"""
    global global_loop
    if global_loop is None:
        init.logger.error("事件循环尚未启动，无法添加任务到队列")
        return False
    
    try:
        future = asyncio.run_coroutine_threadsafe(
            message_queue.put((sub_user, post_url, message, keyboard)),
            global_loop 
        )
        future.result(timeout=30)  # 等待任务添加到队列，设置超时时间
        init.logger.debug(f"任务已添加到队列: {sub_user}, {post_url}, {message}")
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
            task_data = await message_queue.get()
            
            # 兼容旧版本的三参数格式
            if len(task_data) == 3:
                sub_user, post_url, message = task_data
                keyboard = None
            else:
                sub_user, post_url, message, keyboard = task_data
                
            init.logger.info(f"从消息队列中取出任务: 用户[{sub_user}], 链接[{post_url}], 消息[{message}]")
            
            # 检查键盘数据
            if keyboard:
                init.logger.info(f"键盘数据: {keyboard}")
                # 检查callback_data长度
                for row in keyboard.inline_keyboard:
                    for button in row:
                        if button.callback_data and len(button.callback_data) > 64:
                            init.logger.error(f"按钮数据过长: {len(button.callback_data)} bytes - {button.callback_data[:100]}...")
            
            # 根据是否有图片和键盘选择发送方式
            if post_url:
                # 发送图片消息
                await bot.send_photo(
                    chat_id=sub_user,
                    photo=post_url,
                    caption=message,
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard
                )
            else:
                # 发送纯文本消息
                await bot.send_message(
                    chat_id=sub_user,
                    text=message,
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard
                )
                
            init.logger.info(f"消息已发送至 {sub_user}")
            # 标记任务完成
            message_queue.task_done()
            # 间隔防止速率限制
            await asyncio.sleep(3)
        except Exception as e:
            init.logger.error(f"队列任务处理失败: {e}")
        