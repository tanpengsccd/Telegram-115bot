# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, CallbackQueryHandler
from telegram.error import TelegramError
import init
from cover_capture import get_av_cover



SELECT_MAIN_CATEGORY, SELECT_SUB_CATEGORY = range(60, 62)

async def start_av_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️对不起，您无权使用115机器人！")
        return ConversationHandler.END

    if context.args:
        av_number = " ".join(context.args)
        context.user_data["av_number"] = av_number  # 将用户参数存储起来
    else:
        await update.message.reply_text("⚠️请在'/av '命令后输入车牌！")
        return ConversationHandler.END
    # 显示主分类（电影/剧集）
    keyboard = [
        [InlineKeyboardButton(category["display_name"], callback_data=category["name"])] for category in
        init.bot_config['category_folder']
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❓请选择要保存到哪个分类：",
                                   reply_markup=reply_markup)
    return SELECT_MAIN_CATEGORY


async def select_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_main_category = query.data
    if selected_main_category == "return":
        # 显示主分类
        keyboard = [
            [InlineKeyboardButton(category["display_name"], callback_data=category["name"])]
            for category in init.bot_config['category_folder']
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="❓请选择要保存到哪个分类：",
                                       reply_markup=reply_markup)
        return SELECT_MAIN_CATEGORY
    else:
        context.user_data["selected_main_category"] = selected_main_category
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == selected_main_category
        ][0]

        # 创建子分类按钮
        keyboard = [
            [InlineKeyboardButton(category["name"], callback_data=category["path"])] for category in sub_categories
        ]
        keyboard.append([InlineKeyboardButton("返回", callback_data="return")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("❓请选择分类保存目录：", reply_markup=reply_markup)

        return SELECT_SUB_CATEGORY


async def select_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 获取用户选择的路径
    selected_path = query.data
    if selected_path == "return":
        return await select_main_category(update, context)
    av_number = context.user_data["av_number"]
    context.user_data["selected_path"] = selected_path
    # 抓取磁力
    av_result = get_av_result(av_number)
    for item in av_result:
        title = item['title']
        magnet = item['magnet']
        
        # 离线下载到115
        offline_success = init.openapi_115.offline_download_specify_path(magnet, selected_path)
        
        if offline_success:
            await query.edit_message_text(f"✅{title} 已添加到离线下载队列！")
        else:
            await query.edit_message_text(f"❌{title} 添加离线下载失败！")
        download_success, resource_name = init.openapi_115.check_offline_download_success(magnet)
        if download_success:
            init.logger.info(f"✅{title} 离线下载成功！")
            # 按照AV番号重命名
            if resource_name != av_number.upper():
                old_name = f"{selected_path}/{resource_name}"
                init.openapi_115.rename(old_name, av_number.upper())
            # 删除垃圾
            init.openapi_115.auto_clean(f"{selected_path}/{av_number.upper()}")
            # 提取封面
            cover_url, title = get_av_cover(av_number.upper())
            if cover_url:
                try:
                    init.logger.info(f"cover_url: {cover_url}")
                    if title:
                        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=cover_url, caption=title)
                    else:
                        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=cover_url, caption=resource_name)
                except TelegramError as e:
                    init.logger.warn(f"Telegram API error: {e}")
                except Exception as e:
                    init.logger.warn(f"Unexpected error: {e}")
            # 发送通知
            message = f"""
                {item['title']}下载完成！\n保存目录：{selected_path}/{av_number.upper()}
            """
            message = init.escape_markdown_v2(message)
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                text=message,
                                parse_mode='MarkdownV2')
            return ConversationHandler.END
        else:
            init.logger.info(f"❌{title} 离线下载失败, 继续尝试下一个磁力！")
            # 删除失败的离线任务
            init.openapi_115.clear_failed_task(magnet)
    
    if av_result:
        # 全部下载失败
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                    text="**😭全部下载失败，请稍后重试！**",
                                    parse_mode='MarkdownV2')
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                    text="**😵‍💫很遗憾，没有找到对应磁力~**",
                                    parse_mode='MarkdownV2')
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