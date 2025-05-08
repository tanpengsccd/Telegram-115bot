# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
import init
import os
import shutil
from datetime import datetime
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


SELECT_MAIN_CATEGORY_VIDEO, SELECT_SUB_CATEGORY_VIDEO = range(20, 22)


async def save_video2115(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️对不起，您无权使用115机器人！")
        return ConversationHandler.END
    if not init.initialize_115client():
        await update.message.reply_text(f"💀115Cookie已过期，请重新设置！")
        return ConversationHandler.END
    if update.message and update.message.video:
        context.user_data['video'] = update.message.video
        # 显示主分类（电影/剧集）
        keyboard = [
            [InlineKeyboardButton(category["display_name"], callback_data=category["name"])] for category in
            init.bot_config['category_folder']
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❓请选择要保存到哪个分类：",
                                       reply_markup=reply_markup)
        return SELECT_MAIN_CATEGORY_VIDEO


async def select_main_category_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_main_category = query.data
    if selected_main_category == "return":
        # 显示主分类
        keyboard = [
            [InlineKeyboardButton(category["display_name"], callback_data=category["name"])]
            for category in init.bot_config['category_folder']
        ]
        keyboard.append([InlineKeyboardButton("退出", callback_data="quit")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                    text="❓请选择要保存到哪个分类：",
                                    reply_markup=reply_markup)
        return SELECT_MAIN_CATEGORY_VIDEO
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
        return SELECT_SUB_CATEGORY_VIDEO
    

async def select_sub_category_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # 获取用户选择的路径
    selected_path = query.data
    if selected_path == "return":
        return await select_main_category_video(update, context)
    if selected_path == "quit":
        return await quit_conversation(update, context)
    video = context.user_data["video"]
    # 获取视频的 file_id 和文件名
    file_id = video.file_id
    file_name = video.file_name  # 获取文件名（如果有）

    if not file_name:
        file_name = datetime.now().strftime("%Y%m%d%H%M%S") + ".mp4"
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"😼收到视频文件: [{file_name}] \n正在下载中...")

    # 下载文件（可选）
    file = await context.bot.get_file(file_id)
    file_path = f"tmp/{file_name}"
    await file.download_to_drive(file_path)
    # 判断视频文件类型
    formate_name = detect_video_format(file_path)
    new_file_path = file_path[:-3] + formate_name
    # 重命名文件
    if file_path != new_file_path:
        os.rename(file_path, new_file_path)
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"✅视频文件[{new_file_path}]下载完成，正在上传至115...")
    # 上传至115
    response = init.client_115.upload(new_file_path)
    if response.get('status') == 2 and response.get('statuscode') == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚡已秒传！")
        init.client_115.move_file(f"/{os.path.basename(new_file_path)}", selected_path)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅已上传。")
        init.client_115.move_file(f"/{os.path.basename(new_file_path)}", selected_path)
    # 删除本地文件
    for filename in os.listdir("tmp"):
        file_path = os.path.join("tmp", filename)
        if os.path.isfile(file_path):
            os.remove(file_path)  # 删除文件
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话.")
    return ConversationHandler.END


def detect_video_format(file_path):
    # 定义魔数字典，存储视频格式及其对应魔数
    video_signatures = {
        "mp4": [b"\x00\x00\x00\x18\x66\x74\x79\x70", b"\x00\x00\x00\x20\x66\x74\x79\x70"],
        "avi": [b"\x52\x49\x46\x46", b"\x41\x56\x49\x20"],
        "mkv": [b"\x1A\x45\xDF\xA3"],
        "flv": [b"\x46\x4C\x56"],
        "mov": [b"\x00\x00\x00\x14\x66\x74\x79\x70\x71\x74\x20\x20", b"\x6D\x6F\x6F\x76"],
        "wmv": [b"\x30\x26\xB2\x75\x8E\x66\xCF\x11"],
        "webm": [b"\x1A\x45\xDF\xA3"],
    }

    with open(file_path, "rb") as f:
        file_header = f.read(12)  # 读取前12个字节以匹配文件签名

    # 识别文件格式
    for format_name, signatures in video_signatures.items():
        if any(file_header.startswith(signature) for signature in signatures):
            return format_name
    return "unknown"


def register_video_handlers(application):
    # 转存视频
    video_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO, save_video2115)],
        states={
            SELECT_MAIN_CATEGORY_VIDEO: [CallbackQueryHandler(select_main_category_video)],
            SELECT_SUB_CATEGORY_VIDEO: [CallbackQueryHandler(select_sub_category_video)],
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(video_handler)
