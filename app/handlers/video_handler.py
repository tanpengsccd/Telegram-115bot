# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
import init
import os
import shutil
from datetime import datetime
from pathlib import Path
import hashlib
import uuid
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
from app.utils.reaction import REACTION_DONE, REACTION_ERROR, set_message_reaction_safe
from app.utils.task_tracker import register_task, update_task, update_task_item, finish_task

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


SELECT_MAIN_CATEGORY_VIDEO, SELECT_SUB_CATEGORY_VIDEO = range(20, 22)


async def save_video2115(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return ConversationHandler.END
    
    if not init.tg_user_client:
        await update.message.reply_text("⚠️ 如需使用此功能，请先配置[bot_name],[tg_app_id]和[tg_app_hash]！")
        return ConversationHandler.END

    if update.message and update.message.video:
        context.user_data['video'] = {
            "file_name": update.message.document.file_name if update.message.document else (update.message.video.file_name if update.message.video else None),
            "chat_id": update.effective_chat.id,
            "message_id": update.message.message_id,
        }
        # 显示主分类（电影/剧集）
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])] for category in
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
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])]
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
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=category['path'])] for category in sub_categories
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
    
    # 取存储好的chat_id, message_id, file_name
    video = context.user_data["video"]
    file_name = video.get("file_name")
    if not file_name:
        file_name = datetime.now().strftime("%Y%m%d%H%M%S") + ".mp4"
    file_path = f"{init.TEMP}/{file_name}"
    orig_chat_id = video.get("chat_id")
    orig_msg_id = video.get("message_id")
    upload_task_id = f"upload_{uuid.uuid4().hex[:8]}"
    register_task(
        upload_task_id,
        task_type="upload",
        title=file_name,
        total=1,
        save_path=selected_path,
        user_id=update.effective_user.id if update.effective_user else None,
        stage="从Telegram下载视频",
        items=[{"index": 1, "name": file_name, "status": "running", "percent": 10}],
        chat_id=orig_chat_id or update.effective_chat.id,
        origin_message_id=orig_msg_id,
    )
    reply_params = (
        ReplyParameters(message_id=orig_msg_id, allow_sending_without_reply=True)
        if orig_msg_id
        else None
    )

    is_upload = False
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"😼收到视频文件: [{file_name}] \n正在下载中...")
        
        saved_path = file_path
        async with init.tg_user_client:
            # 获取最后一条视频消息
            msgs = await init.tg_user_client.get_messages(init.bot_config['bot_name'], limit=5)
            for msg in msgs:
                if msg.media:
                    saved_path = await init.tg_user_client.download_media(msg, file=file_path)
                    break
        
        
        # 判断视频文件类型
        formate_name = detect_video_format(saved_path)
        new_file_path = saved_path[:-3] + formate_name
        if saved_path != new_file_path:
            os.rename(saved_path, new_file_path)
        update_task(upload_task_id, stage="上传至115网盘")
        update_task_item(upload_task_id, 1, status="running", percent=60)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"✅ 视频文件[{new_file_path}]下载完成，正在上传至115...")
        file_size = os.path.getsize(new_file_path)
        # 计算文件的SHA1值
        sha1_value = file_sha1(new_file_path)
        # 上传至115
        is_upload, bingo = init.openapi_115.upload_file(target=selected_path,
                                           file_name=file_name,
                                           file_size=file_size,
                                           fileid=sha1_value,
                                           file_path=new_file_path,
                                           request_times=1)
        if is_upload:
            update_task_item(upload_task_id, 1, status="success", percent=100)
            if bingo:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⚡ 视频 [{file_name}] 已秒传至 {selected_path}！",
                    reply_parameters=reply_params,
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ 视频 [{file_name}] 已上传完成，保存路径：{selected_path}",
                    reply_parameters=reply_params,
                )
        else:
            update_task_item(upload_task_id, 1, status="failed", error="115上传失败")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ 视频 [{file_name}] 上传失败！",
                reply_parameters=reply_params,
            )
    except Exception as e:
        init.logger.error(f"视频上传处理失败: {e}", exc_info=True)
        update_task_item(upload_task_id, 1, status="failed", error=str(e)[:60])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ 上传异常：{e}",
            reply_parameters=reply_params,
        )
    finally:
        finish_task(
            upload_task_id,
            status="completed" if is_upload else "failed",
            note="上传完成" if is_upload else "上传失败",
        )
        if orig_chat_id and orig_msg_id:
            await set_message_reaction_safe(
                context.bot,
                orig_chat_id,
                orig_msg_id,
                REACTION_DONE if is_upload else REACTION_ERROR,
            )
        # 删除本地文件
        for filename in os.listdir(init.TEMP):
            fp = os.path.join(init.TEMP, filename)
            if os.path.isfile(fp):
                os.remove(fp)
            elif os.path.isdir(fp):
                shutil.rmtree(fp)

    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
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

def file_sha1(file_path):
    with open(file_path, 'rb') as f:
        return hashlib.sha1(f.read()).hexdigest()


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
    init.logger.info("✅ Video处理器已注册")
    


