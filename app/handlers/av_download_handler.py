# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, CallbackQueryHandler
from telegram.error import TelegramError
import init
from concurrent.futures import ThreadPoolExecutor
from app.utils.cover_capture import get_av_cover
from telegram.helpers import escape_markdown

# 全局线程池，用于处理下载任务
download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="AV_Download")



SELECT_MAIN_CATEGORY, SELECT_SUB_CATEGORY = range(60, 62)

async def start_av_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text(" 对不起，您无权使用115机器人！")
        return ConversationHandler.END

    if context.args:
        av_number = " ".join(context.args)
        context.user_data["av_number"] = av_number  # 将用户参数存储起来
    else:
        await update.message.reply_text("⚠️ 请在'/av '命令后输入车牌！")
        return ConversationHandler.END
    # 显示主分类（电影/剧集）
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])] for category in
        init.bot_config['category_folder']
    ]
    # 只在有最后保存路径时才显示该选项
    if hasattr(init, 'bot_session') and "av_last_save" in init.bot_session:
        last_save_path = init.bot_session['av_last_save']
        keyboard.append([InlineKeyboardButton(f"📁 上次保存: {last_save_path}", callback_data="last_save_path")])
    keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❓请选择要保存到哪个分类：",
                                   reply_markup=reply_markup)
    return SELECT_MAIN_CATEGORY


async def select_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_main_category = query.data
    if selected_main_category == "cancel":
        return await quit_conversation(update, context)
    elif selected_main_category == "last_save_path":
        # 直接使用最后一次保存的路径
        if hasattr(init, 'bot_session') and "av_last_save" in init.bot_session:
            last_path = init.bot_session['av_last_save']
            av_number = context.user_data["av_number"]
            context.user_data["selected_path"] = last_path
            user_id = update.effective_user.id
            
            # 抓取磁力
            await query.edit_message_text(f"🔍 正在搜索 [{av_number}] 的磁力链接...")
            av_result = get_av_result(av_number)
            
            if not av_result:
                await query.edit_message_text(f"😵‍💫很遗憾，没有找到{av_number.upper()}的对应磁力~")
                return ConversationHandler.END
            
            # 立即反馈用户
            await query.edit_message_text(f"✅ [{av_number}] 已为您添加到下载队列！\n保存路径: {last_path}\n请稍后~")
            
            # 使用全局线程池异步执行下载任务
            download_executor.submit(download_task, av_result, av_number, last_path, user_id)
            
            return ConversationHandler.END
        else:
            await query.edit_message_text("❌ 未找到最后一次保存路径，请重新选择分类")
            return ConversationHandler.END
    else:
        context.user_data["selected_main_category"] = selected_main_category
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == selected_main_category
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
    if selected_path == "cancel":
        return await quit_conversation(update, context)
    
    av_number = context.user_data["av_number"]
    context.user_data["selected_path"] = selected_path
    user_id = update.effective_user.id
    
    # 保存最后一次使用的路径
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    init.bot_session['av_last_save'] = selected_path
    
    # 抓取磁力
    await query.edit_message_text(f"🔍 正在搜索 [{av_number}] 的磁力链接...")
    av_result = get_av_result(av_number)
    
    if not av_result:
        await query.edit_message_text(f"😵‍💫很遗憾，没有找到{[av_number.upper()]}的对应磁力~")
        return ConversationHandler.END
    
    # 立即反馈用户
    await query.edit_message_text(f"✅ [{av_number}] 已为您添加到下载队列！\n请稍后~")
    
    # 使用全局线程池异步执行下载任务
    download_executor.submit(download_task, av_result, av_number, selected_path, user_id)
    
    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
    return ConversationHandler.END


def get_av_result(av_number):
    result = []
    url = f"https://sukebei.nyaa.si/?q={av_number}&f=0&c=0_0"
    response = requests.get(url)
    if response.status_code != 200:
        return result
    soup = BeautifulSoup(response.text, 'html.parser')
    for tr in soup.find_all('tr', class_='default'):
        # 提取标题
        title_a = tr.find('a', href=lambda x: x and x.startswith('/view/'))
        title = title_a.get_text(strip=True) if title_a else "No title found"
        
        # 提取磁力链接
        magnet_a = tr.find('a', href=lambda x: x and x.startswith('magnet:'))
        magnet = magnet_a['href'] if magnet_a else "No magnet found"
        
        result.append({
            'title': title,
            'magnet': magnet
        })
    return result

def download_task(av_result, av_number, save_path, user_id):
    """异步下载任务"""
    try:
        for item in av_result:
            magnet = item['magnet']
            title = item['title']
            # 离线下载到115
            offline_success = init.openapi_115.offline_download_specify_path(magnet, save_path)
            if not offline_success:
                continue
            
            # 检查下载状态
            download_success, resource_name = init.openapi_115.check_offline_download_success(magnet)
            
            if download_success:
                init.logger.info(f"✅ {av_number} 离线下载成功！")
                
                # 按照AV番号重命名
                if resource_name != av_number.upper():
                    old_name = f"{save_path}/{resource_name}"
                    init.openapi_115.rename(old_name, av_number.upper())
                
                # 删除垃圾
                init.openapi_115.auto_clean(f"{save_path}/{av_number.upper()}")
                
                # 提取封面
                cover_url, title = get_av_cover(av_number.upper())
                msg_av_number = escape_markdown(f"#{av_number.upper()}", version=2)
                av_title = escape_markdown(title, version=2)
                msg_title = escape_markdown(f"[{av_number.upper()}] 下载完成", version=2)
                # 发送成功通知
                message = f"""
**{msg_title}**

**番号:** `{msg_av_number}`
**标题:** `{av_title}`
**保存目录:** `{save_path}/{av_number.upper()}`
                """           
                from app.utils.message_queue import add_task_to_queue
                if not init.aria2_client:
                    add_task_to_queue(user_id, cover_url, message)
                else:
                    push2aria2(f"{save_path}/{av_number.upper()}", user_id, cover_url, message)
                return  # 成功后直接返回
            else:
                # 删除失败的离线任务
                init.openapi_115.clear_failed_task(magnet)
        
        # 如果循环结束都没有成功，发送失败通知
        init.logger.info(f"❌ {av_number} 所有磁力链接都下载失败")
        from app.utils.message_queue import add_task_to_queue
        add_task_to_queue(user_id, None, f"❌ [{av_number}] 所有磁力链接都下载失败，请稍后重试！")
        
    except Exception as e:
        init.logger.warn(f"💀下载遇到错误: {str(e)}")
        from app.utils.message_queue import add_task_to_queue
        add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/male023.png",
                            message=f"❌ 下载任务执行出错: {str(e)}")
    finally:
        # ⚠️ 注意：clear_cloud_task() 会清空所有离线任务（包括其他用户/批量任务）
        # 建议：应该只清理当前 AV 下载任务（通过 del_faild_offline_task + info_hash）
        # TODO: 改为只清理本次尝试的磁力链接任务，避免影响其他任务
        init.openapi_115.clear_cloud_task()
        
def push2aria2(save_path, user_id, cover_image, message):
    # 为Aria2推送创建任务ID系统
    import uuid
    push_task_id = str(uuid.uuid4())[:8]
    
    # 初始化pending_push_tasks（如果不存在）
    if not hasattr(init, 'pending_push_tasks'):
        init.pending_push_tasks = {}
    
    # 存储推送任务数据
    init.pending_push_tasks[push_task_id] = {
        'path': save_path
    }
    
    device_name = init.bot_config.get('aria2', {}).get('device_name', 'Aria2') or 'Aria2'
    
    keyboard = [
        [InlineKeyboardButton(f"推送到{device_name}", callback_data=f"push2aria2_{push_task_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    from app.utils.message_queue import add_task_to_queue
    add_task_to_queue(user_id, cover_image, message, reply_markup)
    


def register_av_download_handlers(application):
    # download下载交互
    download_handler = ConversationHandler(
        entry_points=[CommandHandler("av", start_av_command)],
        states={
            SELECT_MAIN_CATEGORY: [CallbackQueryHandler(select_main_category)],
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)]
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_handler)
    init.logger.info("✅ AV Downloader处理器已注册")