# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters
import init


# 定义对话的步骤
ASK_COOKIE, RECEIVE_COOKIE = range(0, 2)


async def set_115cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if init.check_user(usr_id):
        await context.bot.send_message(chat_id=update.effective_chat.id, 
                                       text="💡请发送115Cookie，格式: UID=xxxxxx; CID=xxxxxx; SEID=xxxxxx")
        return RECEIVE_COOKIE
    else:
        await update.message.reply_text(f"⚠️对不起，您无权使用115机器人！")


async def receive_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 获取用户发送的 cookie
    user_cookie = update.message.text
    if "UID=" not in user_cookie or "SEID=" not in user_cookie or "CID=" not in user_cookie:
        await update.message.reply_text("⚠️Cookie 格式输入有误，请检查！")
    else:
        with open(init.COOKIE_FILE, mode='w', encoding='utf-8') as f:
            f.write(user_cookie)
        # 可以在这里处理或存储 cookie
        await update.message.reply_text(f"✅设置115Cookie成功！")
    # 结束对话
    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚪用户退出本次会话.")
    return ConversationHandler.END


def register_cookie_handlers(application):
    cookie_handler = ConversationHandler(
        entry_points=[CommandHandler("cookie", set_115cookie)],
        states={
            RECEIVE_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cookie)],
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(cookie_handler)