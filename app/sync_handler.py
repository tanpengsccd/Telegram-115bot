from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, CallbackQueryHandler
import init
import shutil
from pathlib import Path
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


SELECT_MAIN_CATEGORY_SYNC, SELECT_SUB_CATEGORY_SYNC = range(30, 32)


async def sync_strm_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️对不起，您无权使用115机器人！")
        return ConversationHandler.END
    if not init.initialize_115client():
        await update.message.reply_text(f"💀115Cookie已过期，请重新设置！")
        return ConversationHandler.END

    # 显示主分类（电影/剧集）
    keyboard = [
        [InlineKeyboardButton(category["display_name"], callback_data=category["name"])] for category in
        init.bot_config['category_folder']
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❓请选择要同步的分类：",
                                   reply_markup=reply_markup)
    return SELECT_MAIN_CATEGORY_SYNC


async def select_main_category_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                                       text="❓请选择要同步的分类：",
                                       reply_markup=reply_markup)
        return SELECT_MAIN_CATEGORY_SYNC
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
        await query.edit_message_text("❓请选择要同步的目录：", reply_markup=reply_markup)
        return SELECT_SUB_CATEGORY_SYNC
    

async def select_sub_category_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # 获取用户选择的路径 "/影视/电影/外语电影/"
    selected_path = query.data
    if selected_path == "return":
        return await select_main_category_sync(update, context)
    mount_root = Path(init.bot_config['mount_root'])
    strm_root = Path(init.bot_config['strm_root'])
    init.logger.debug(f"selected_path: {selected_path}")
    try:
        # 递归删除所有
        sync_path = strm_root / Path(selected_path).relative_to("/")
        if sync_path.exists() and sync_path.is_dir():
            shutil.rmtree(str(sync_path))

        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"🔄[{selected_path}]正在同步strm文件，请稍后...")
        video_files = []
        init.client_115.get_file_from_path(selected_path, video_files)
        for file in video_files:
            file_path = Path(file)
            video_path = mount_root / file_path.relative_to("/")
            strm_path = strm_root / file_path.parent.relative_to("/")
            if not strm_path.exists():
                strm_path.mkdir(parents=True, exist_ok=True)
            strm_content = str(video_path)
            strm_file = strm_path / (Path(file).stem + ".strm")
            with open(strm_file, 'w') as f:
                f.write(strm_content)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"✅[{selected_path}]strm文件同步完成！")
        return ConversationHandler.END
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"❌同步strm文件失败：{str(e)}！")
        return ConversationHandler.END
    

async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚪用户退出本次会话.")
    return ConversationHandler.END


def register_sync_handlers(application):
    # 同步strm软链
    sync_handler = ConversationHandler(
        entry_points=[CommandHandler("sync", sync_strm_files)],
        states={
            SELECT_MAIN_CATEGORY_SYNC: [CallbackQueryHandler(select_main_category_sync)],
            SELECT_SUB_CATEGORY_SYNC: [CallbackQueryHandler(select_sub_category_sync)],
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
        per_chat=True
    )
    application.add_handler(sync_handler)