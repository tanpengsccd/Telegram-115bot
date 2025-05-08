# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError
import init
import re
import time
from pathlib import Path
from cover_capture import get_movie_cover, get_av_cover
import requests
from enum import Enum
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SELECT_MAIN_CATEGORY, SELECT_SUB_CATEGORY, SPECIFY_NAME = range(10, 13)

class DownloadUrlType(Enum):
    ED2K = "ED2K"
    THUNDER = "thunder"
    HTTP = "http"
    FTP = "ftp"
    MAGNET = "magnet"
    SHARE = "share"
    UNKNOWN = "unknown"
    
    def __str__(self):
        return self.value


async def start_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️对不起，您无权使用115机器人！")
        return ConversationHandler.END
    if not init.initialize_115client():
        await update.message.reply_text(f"💀115Cookie已过期，请重新设置！")
        return ConversationHandler.END
    if context.args:
        magnet_link = " ".join(context.args)
        context.user_data["link"] = magnet_link  # 将用户参数存储起来
        init.logger.info(f"download link: {magnet_link}")
        dl_url_type = is_valid_link(magnet_link)
        # 检查链接格式是否正确
        if dl_url_type == DownloadUrlType.UNKNOWN:
            await update.message.reply_text("⚠️下载链接格式错误，请修改后重试！")
            return ConversationHandler.END
        # 保存下载类型到context.user_data
        context.user_data["dl_url_type"] = dl_url_type
    else:
        await update.message.reply_text("⚠️请在'/dl '命令后输入合法的下载链接！")
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
    link = context.user_data["link"]
    context.user_data["selected_path"] = selected_path
    selected_main_category = context.user_data["selected_main_category"]
    # 下载磁力
    if context.user_data["dl_url_type"] != DownloadUrlType.SHARE:
        response, resource_name = init.client_115.offline_download(link)
        if response.get('errno') is not None:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                        text=f"❌离线遇到错误！error_type: {response.get('errtype')}！")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                        text=f"`{resource_name}`  \n✅添加离线成功",
                                        parse_mode="MarkdownV2")
            download_success = init.client_115.check_offline_download_success(link, resource_name)
            if download_success:
                context.user_data["resource_name"] = resource_name
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                            text=f"`{resource_name}`  \n✅离线下载完成",
                                            parse_mode="MarkdownV2")
                time.sleep(10)

                # 如果下载的内容是目录
                if init.client_115.is_directory(f"{init.bot_config['offline_path']}/{resource_name}"):
                    # 清除垃圾文件
                    init.client_115.auto_clean(f"{init.bot_config['offline_path']}/{resource_name}")
                    # 移动文件
                    init.client_115.move_file(f"{init.bot_config['offline_path']}/{resource_name}", selected_path)
                    context.user_data["old_name"] = f"{selected_path}/{resource_name}"
                # 如果下载的内容是文件，为文件套一个文件夹
                else:
                    init.client_115.create_dir_for_video_file(f"{init.bot_config['offline_path']}/{resource_name}")
                    init.client_115.move_file(f"{init.bot_config['offline_path']}/temp", selected_path)
                    context.user_data["old_name"] = f"{selected_path}/temp"
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                            text=f"`{resource_name}`  \n✅移动到分类文件夹\\[{selected_path}\\]成功",
                                            parse_mode="MarkdownV2")

                await context.bot.send_message(chat_id=update.effective_chat.id,
                                            text=f"🈯请指定标准的资源名称，便于削刮。\\(点击资源名称自动复制\\)  \n\n**`{resource_name}`**",
                                            parse_mode='MarkdownV2')
                # 重命名文件
                return SPECIFY_NAME
            else:
                # 下载超时删除任务
                init.client_115.clear_failed_task(link, resource_name)
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                            text=f"`{resource_name}`  \n😭离线下载超时，建议更换链接重试~",
                                            parse_mode='MarkdownV2')
    else:
        success, files = init.client_115.save_shared_link(init.bot_config['offline_path'], link)
        if success:
            for file in files:
                # 移动到目标目录
                init.client_115.move_file(f"{init.bot_config['offline_path']}/{file}", selected_path)
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                            text=f"`{resource_name}`  \n✅移动到分类文件夹\\[{selected_path}\\]成功",
                                            parse_mode="MarkdownV2")


async def specify_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resource_name = update.message.text
    download_url = context.user_data["link"]
    selected_path = context.user_data["selected_path"]
    old_name = context.user_data["old_name"]
    new_name = f"{selected_path}/{resource_name}"
    # 重命名资源
    init.client_115.rename(old_name, new_name)
    file_list = init.client_115.get_files_from_dir(new_name)
    init.logger.info(file_list)
    # 创建软链
    create_strm_file(new_name, file_list)

    # 发送削刮图片, 如果有的话...
    cover_url = ""
    title = ""
    if context.user_data["selected_main_category"] == "movies":
        cover_url = get_movie_cover(resource_name)
    if context.user_data["selected_main_category"] == "av":
        cover_url, title = get_av_cover(resource_name)
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
            
    # 如果已经订阅过
    from subscribe_movie import is_subscribe, update_subscribe
    if is_subscribe(resource_name):
        # 更新订阅信息
        update_subscribe(resource_name, cover_url, download_url)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"💡订阅影片`{resource_name}`已手动下载!",
                                   parse_mode='MarkdownV2')
        

    # 通知Emby扫库
    notice_emby_scan_library()
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="**👻已通知Emby扫库，请稍后确认！**",
                                   parse_mode='MarkdownV2')
    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话.")
    return ConversationHandler.END


def is_valid_link(link: str) -> DownloadUrlType:    
    # 定义链接模式字典
    patterns = {
        DownloadUrlType.MAGNET: r'^magnet:\?xt=urn:[a-z0-9]+:[a-zA-Z0-9]{32,40}',
        DownloadUrlType.ED2K: r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        DownloadUrlType.THUNDER: r'^thunder://[a-zA-Z0-9=]+',
        DownloadUrlType.HTTP: r'^https?://[^\s/$.?#].[^\s]*',
        DownloadUrlType.FTP: r'^ftp://[^\s/$.?#].[^\s]*'
    }
    
    # 检查基本链接类型
    for url_type, pattern in patterns.items():
        if re.match(pattern, link):
            return url_type

    # 特殊处理115分享链接的两种格式
    share_patterns = [
        # 标准格式: https://115.com/s/abc123?password=1234
        r"(?:/s/|share\.115\.com/)(?P<share_code>[a-z0-9]+)\?password=(?P<receive_code>[a-z0-9]{4})",
        # 简化格式: abc123-1234
        r"(?P<share_code>[a-z0-9]+)-(?P<receive_code>[a-z0-9]{4})"
    ]
    for pattern in share_patterns:
        if re.search(pattern, link):
            return DownloadUrlType.SHARE
        
    return DownloadUrlType.UNKNOWN


def create_strm_file(new_name, file_list):
    # 检查是否需要创建软链
    if not init.bot_config['create_strm']:
        return
    try:
        init.logger.debug(f"Original new_name: {new_name}")

        # 获取根目录
        cd2_mount_root = Path(init.bot_config['mount_root'])
        strm_root = Path(init.bot_config['strm_root'])

        # 构建目标路径和 .strm 文件的路径
        relative_path = Path(new_name).relative_to(Path(new_name).anchor)
        cd2_mount_path = cd2_mount_root.joinpath(relative_path)
        strm_path = strm_root.joinpath(relative_path)

        # 日志输出以验证路径
        init.logger.debug(f"cd2_mount_root: {cd2_mount_root}")
        init.logger.debug(f"strm_root: {strm_root}")
        init.logger.debug(f"cd2_mount_path: {cd2_mount_path}")
        init.logger.debug(f"strm_path: {strm_path}")

        # 确保 strm_path 路径存在
        if not strm_path.exists():
            strm_path.mkdir(parents=True, exist_ok=True)

        # 遍历文件列表，创建 .strm 文件
        for file in file_list:
            target_file = strm_path / (Path(file).stem + ".strm")
            mkv_file = cd2_mount_path / file

            # 日志输出以验证 .strm 文件和目标文件
            init.logger.debug(f"target_file (.strm): {target_file}")
            init.logger.debug(f"mkv_file (.mp4): {mkv_file}")

            # 如果原始文件存在，写入 .strm 文件
            # if mkv_file.exists():
            with target_file.open('w', encoding='utf-8') as f:
                f.write(str(mkv_file))
                init.logger.info(f"strm文件创建成功，{target_file} -> {mkv_file}")
            # else:
            #     init.logger.info(f"原始视频文件[{mkv_file}]不存在！")
    except Exception as e:
        init.logger.info(f"Error creating .strm files: {e}")


def notice_emby_scan_library():
    emby_server = init.bot_config['emby_server']
    api_key = init.bot_config['api_key']
    if str(emby_server).endswith("/"):
        emby_server = emby_server[:-1]
    url = f"{emby_server}/Library/Refresh"
    headers = {
        "X-Emby-Token": api_key
    }
    emby_response = requests.post(url, headers=headers)
    if emby_response.text == "":
        init.logger.info("通知Emby扫库成功！")
    else:
        init.logger.error(f"通知Emby扫库失败：{emby_response}")


def check_cookie():
    cookie_file = Path(init.COOKIE_FILE)
    if not cookie_file.exists():
        return False
    else:
        return True


def register_download_handlers(application):
    # download下载交互
    download_handler = ConversationHandler(
        entry_points=[CommandHandler("dl", start_d_command)],
        states={
            SELECT_MAIN_CATEGORY: [CallbackQueryHandler(select_main_category)],
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)],
            SPECIFY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, specify_name)]
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_handler)