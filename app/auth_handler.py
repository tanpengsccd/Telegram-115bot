# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import CommandHandler, ConversationHandler, ContextTypes
import init


# 定义对话的步骤
# ASK_COOKIE, RECEIVE_COOKIE = range(0, 2)


async def auth_pkce_115(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if init.check_user(usr_id):
        init.openapi_115.auth_pkce(usr_id, init.bot_config['115_app_id'])
        if init.openapi_115.access_token and init.openapi_115.refresh_token:
            await update.message.reply_text("✅授权成功！")
        else:
            await update.message.reply_text("⚠️授权失败，请检查配置文件中的app_id是否正确！")
    else:
        await update.message.reply_text(f"⚠️对不起，您无权使用115机器人！")
    # 结束对话
    return ConversationHandler.END


# async def receive_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     # 获取用户发送的 cookie
#     user_cookie = update.message.text
#     if "UID=" not in user_cookie or "SEID=" not in user_cookie or "CID=" not in user_cookie:
#         await update.message.reply_text("⚠️Cookie 格式输入有误，请检查！")
#     else:
#         with open(init.COOKIE_FILE, mode='w', encoding='utf-8') as f:
#             f.write(user_cookie)
#         # 可以在这里处理或存储 cookie
#         await update.message.reply_text(f"✅设置115Cookie成功！")
#     # 结束对话
#     return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
    return ConversationHandler.END


def register_auth_handlers(application):
    auth_handler = ConversationHandler(
        entry_points=[CommandHandler("auth", auth_pkce_115)],
        states={},  # 添加空的states字典
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(auth_handler)