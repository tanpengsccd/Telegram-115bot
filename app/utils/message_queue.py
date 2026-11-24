# -*- coding: utf-8 -*-

import os
import asyncio
import init
from telegram import Bot, ReplyParameters
from telegram.request import HTTPXRequest

# 全局消息队列（延迟初始化）
message_queue = None
# 全局变量，用于存储事件循环和Bot实例
global_loop = None
global_bot = None


def add_task_to_queue(sub_user, post_url, message, keyboard=None, retry_count=0, reply_to_message_id=None):
    """向消息队列中添加任务（线程安全，支持引用原消息 reply_to_message_id）"""
    global global_loop, message_queue
    if global_loop is None or message_queue is None:
        init.logger.error("事件循环或消息队列尚未初始化，无法添加任务到队列")
        return False
    
    try:
        future = asyncio.run_coroutine_threadsafe(
            message_queue.put((sub_user, post_url, message, keyboard, retry_count, reply_to_message_id)),
            global_loop 
        )
        future.result(timeout=30)  # 等待任务添加到队列，设置超时时间
        init.logger.debug(f"任务已添加到队列: {sub_user}, {post_url}, reply={reply_to_message_id}, {message}")
        return True
    except TimeoutError:
        init.logger.error(f"添加任务到队列超时: {sub_user}, {post_url}, {message}")
        return False
    except Exception as e:
        init.logger.error(f"添加任务到队列失败: {e}")
        return False


def send_document_via_bot(chat_id, file_path, caption="", reply_to_message_id=None, reply_markup=None):
    """线程安全地发送文档消息（优先复用全局 Bot + 事件循环以继承代理/连接池，并支持引用原消息）"""
    global global_loop, global_bot
    reply_params = (
        ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
        if reply_to_message_id
        else None
    )

    async def _send(bot_instance):
        with open(file_path, 'rb') as f:
            await bot_instance.send_document(
                chat_id=chat_id,
                document=f,
                caption=caption,
                reply_parameters=reply_params,
                reply_markup=reply_markup,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10,
                pool_timeout=10,
            )

    if global_loop is not None and global_bot is not None:
        future = asyncio.run_coroutine_threadsafe(_send(global_bot), global_loop)
        future.result(timeout=60)
        return True

    # 回退方案（例如单元测试或独立脚本未启动 queue_worker）
    import httpx
    proxy = (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip() or None
    request = HTTPXRequest(
        connection_pool_size=4,
        proxy=proxy,
        connect_timeout=10,
        pool_timeout=10,
        httpx_kwargs={"limits": httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=15.0)},
    )
    bot = Bot(token=init.get_bot_token(), request=request)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_send(bot))
    finally:
        loop.close()
    return True
        
        
async def queue_worker(loop, token):
    global global_loop, message_queue, global_bot
    """ 后台队列处理任务 """
    global_loop = loop

    # 在正确的事件循环中初始化队列
    if message_queue is None:
        message_queue = asyncio.Queue()

    # bot：proxy仅在配置了HTTP_PROXY/HTTPS_PROXY时生效；keepalive_expiry避免空闲连接复用报502
    import httpx
    proxy = (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip() or None
    request = HTTPXRequest(
        connection_pool_size=8,
        proxy=proxy,
        connect_timeout=10,
        pool_timeout=10,
        httpx_kwargs={"limits": httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=15.0)},
    )
    bot = Bot(token=token, request=request)
    global_bot = bot
    init.logger.info("消息队列线程启动成功！")
    while True:
        retry_count = 0  # 在try块外初始化retry_count
        reply_to_message_id = None
        try:
            # 从队列获取任务
            task_data = await message_queue.get()
            
            # 兼容不同版本的任务格式
            if len(task_data) == 3:
                # 旧版本：三参数格式
                sub_user, post_url, message = task_data
                keyboard = None
                retry_count = 0
                reply_to_message_id = None
            elif len(task_data) == 4:
                # 中版本：四参数格式
                sub_user, post_url, message, keyboard = task_data
                retry_count = 0
                reply_to_message_id = None
            elif len(task_data) == 5:
                # 五参数格式（带重试计数）
                sub_user, post_url, message, keyboard, retry_count = task_data
                reply_to_message_id = None
            else:
                # 新版本：六参数格式（带重试计数与引用消息ID）
                sub_user, post_url, message, keyboard, retry_count, reply_to_message_id = task_data[:6]
                
            retry_suffix = f" (重试 {retry_count}/3)" if retry_count > 0 else ""
            # 显示链接时，如果post_url为None，尝试从message中提取下载链接
            display_link = post_url
            if not display_link and message:
                # 尝试从消息中提取各种类型的链接
                import re
                # 匹配 HTTP(S)、FTP、磁力链、电驴链接
                link_patterns = [
                    r'(https?://[^\s\]]+)',
                    r'(ftp://[^\s\]]+)',
                    r'(ed2k://[^\s\]]+)',
                    r'(magnet:\?[^\s\]]+)',
                    r'(thunder://[^\s\]]+)'
                ]
                for pattern in link_patterns:
                    link_match = re.search(pattern, message, re.IGNORECASE)
                    if link_match:
                        found_link = link_match.group(1)
                        display_link = found_link[:100] + "..." if len(found_link) > 100 else found_link
                        break
            init.logger.info(f"从消息队列中取出任务{retry_suffix}: 用户[{sub_user}], 链接[{display_link}], 消息[{message}]")
            
            # 检查键盘数据
            if keyboard:
                init.logger.info(f"键盘数据: {keyboard}")
                # 检查callback_data长度
                for row in keyboard.inline_keyboard:
                    for button in row:
                        if button.callback_data and len(button.callback_data) > 64:
                            init.logger.error(f"按钮数据过长: {len(button.callback_data)} bytes - {button.callback_data[:100]}...")
            
            # 构造引用回复参数（若原消息已被删除仍允许正常发送）
            reply_params = (
                ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
                if reply_to_message_id
                else None
            )

            # 根据是否有图片和键盘选择发送方式
            if post_url:
                # 发送图片消息（显式超时避免长连接卡死）
                await bot.send_photo(
                    chat_id=sub_user,
                    photo=post_url,
                    caption=message,
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard,
                    reply_parameters=reply_params,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=10,
                    pool_timeout=10
                )
            else:
                # 发送纯文本消息
                await bot.send_message(
                    chat_id=sub_user,
                    text=message,
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard,
                    reply_parameters=reply_params,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=10,
                    pool_timeout=10
                )
                
            init.logger.info(f"消息已发送至 {sub_user}")
            # 标记任务完成
            message_queue.task_done()
            # 间隔防止速率限制
            await asyncio.sleep(3)
        except Exception as e:
            # 处理发送失败的情况
            error_msg = str(e)
            is_timeout = "Timed out" in error_msg or "TimeoutError" in error_msg
            if is_timeout:
                init.logger.warn(f"消息发送超时 (尝试 {retry_count + 1}/3): {error_msg}，但消息可能已成功发送")
            else:
                init.logger.warn(f"队列任务处理失败 (尝试 {retry_count + 1}/3): {error_msg}")

            try:
                # 检查是否为不可重试的错误
                non_retryable_errors = [
                    "Invalid file http url specified: url host is empty",
                    "Invalid file http url specified",
                    "Bad Request: invalid file URL",
                    "Bad Request: wrong file identifier/HTTP URL specified",
                    "Forbidden: bot was blocked by the user",
                    "Bad Request: chat not found",
                    "Bad Request: user is deactivated"
                ]

                should_retry = True
                for non_retryable in non_retryable_errors:
                    if non_retryable in error_msg:
                        should_retry = False
                        init.logger.error(f"❌ 遇到不可重试错误，直接放弃: {error_msg}")
                        break

                # 检查是否需要重试
                if should_retry and retry_count < 2:  # 最多重试2次（总共3次尝试）
                    # 重新入队，增加重试计数并保留引用消息ID
                    new_retry_count = retry_count + 1
                    await message_queue.put((sub_user, post_url, message, keyboard, new_retry_count, reply_to_message_id))
                    init.logger.info(f"任务已重新入队，将进行第 {new_retry_count + 1} 次尝试")

                    # 重试前等待一段时间（指数退避）
                    retry_delay = 5 * (2 ** retry_count)  # 5秒, 10秒, 20秒
                    await asyncio.sleep(retry_delay)
                else:
                    # 超过最大重试次数或遇到不可重试错误，记录最终失败
                    if should_retry:
                        init.logger.error(f"❌ 任务重试次数已达上限，放弃重试: 用户[{sub_user}], 错误: {error_msg}")
                    else:
                        init.logger.error(f"❌ 任务因不可重试错误直接放弃: 用户[{sub_user}], 错误: {error_msg}")
                    init.logger.error(f"❌ 失败消息内容: {message}")

                # 标记当前任务完成
                message_queue.task_done()

            except Exception as retry_error:
                # 如果重试处理失败，也要标记任务完成
                init.logger.error(f"重试处理失败: {retry_error}")
                try:
                    message_queue.task_done()
                except Exception:
                    pass  # 忽略task_done的错误
        