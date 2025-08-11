# -*- coding: utf-8 -*-

import os
import yaml
from logger import Logger
from typing import Optional
from telethon import TelegramClient
from sqlitelib import *


# 全局日志
logger:Optional[Logger] = None

# 全局配置
bot_config = dict()

# 115客户端对象
# client_115 = None

# 115开放API对象
openapi_115 = None

# Tg 用户客户端
tg_user_client: Optional[TelegramClient] = None

# yaml配置文件目录
CONFIG_FILE = "/config/config.yaml"

# 115Cookie File
COOKIE_FILE = "/config/cookie.txt"

# SessionFile
TG_SESSION_FILE = "/config/user_session.session"

# DB File
DB_FILE = "/config/db.db"

TEMP = "/tmp"

# 115 Token File
TOKEN_FILE = "/config/115_tokens.json"

IMAGE_PATH = "/app/images"

JAVDB_COOKIE = ""
USER_AGENT = ""

# 调试用
# CONFIG_FILE = "config/config.yaml"
# COOKIE_FILE = "config/cookie.txt"
# TG_SESSION_FILE = "config/user_session.session"
# DB_FILE = "config/db.db"
# TOKEN_FILE = "config/115_tokens.json"
# IMAGE_PATH = "app/images"
# TEMP = "tmp"


def create_logger():
    """
    创建全局日志对象
    :return:
    """
    global logger
    # 全局日志实例，输出到命令行和文件
    logger = Logger()
    logger.info("Logger init success!")


def load_yaml_config():
    """
    读取配置文件
    :return:
    """
    global bot_config, USER_AGENT, JAVDB_COOKIE, CONFIG_FILE
    yaml_path = CONFIG_FILE
    # 获取yaml文件名称
    try:
        # 获取yaml文件路径
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                cfg = f.read()
                f.close()
            bot_config = yaml.load(cfg, Loader=yaml.FullLoader)
            USER_AGENT = bot_config['subscribe']['user_agent']
            JAVDB_COOKIE = bot_config['subscribe']['javdb_cookie']
            # return bot_config
        else:
            logger.error("Config file not found!")
    except Exception as e:
        logger.error(f"配置文件[{yaml_path}]格式有误，请检查!")


def get_bot_token():
    global CONFIG_FILE, bot_config
    bot_token = ""
    if 'bot_token' in bot_config.keys():
        bot_token = bot_config['bot_token']
    else:
        yaml_path = CONFIG_FILE
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                cfg = f.read()
                f.close()
            bot_config = yaml.load(cfg, Loader=yaml.FullLoader)
            bot_token = bot_config['bot_token']
        return bot_token

def create_tmp():
    if not os.path.exists(TEMP):
        os.mkdir(TEMP, mode=0o777)
        os.chmod(TEMP, 0o777)

def initialize_tg_usr_client():
    """
    初始化Tg用户客户端
    :param session_name: session文件名，默认为'user_session'
    :param session_dir: session文件保存目录，默认为当前目录
    :return: bool - 初始化是否成功
    """
    global tg_user_client, bot_config, logger
    try:
        if not (bot_config['tg_api_id'] and bot_config['tg_api_hash'] and bot_config['bot_name']):
            logger.warn("缺少必要的Telegram API配置 (tg_api_id & tg_api_hash & bot_name), 无法使用视频上传功能。")
            tg_user_client = None
            return False
            
        api_id = bot_config['tg_api_id']
        api_hash = bot_config['tg_api_hash']

        if os.path.exists(TG_SESSION_FILE):
            tg_user_client = TelegramClient(TG_SESSION_FILE, api_id, api_hash)
            logger.info(f"Telegram User Client 初始化成功，session路径: {TG_SESSION_FILE}")
            return True
        
    except Exception as e:
        logger.error(f"Telegram User Client initialization failed: {e}")
        tg_user_client = None
        return False
    
def initialize_115open():
    """
    初始化115开放API客户端
    :return: bool - 初始化是否成功
    """
    global openapi_115, logger
    try:
        from open_115 import OpenAPI_115
        openapi_115 = OpenAPI_115()
        # 检查是否成功获取到token
        if openapi_115.access_token and openapi_115.refresh_token:
            logger.info("115开放API客户端初始化成功")
            return True
        else:
            logger.error("115开放API客户端初始化失败: 无法获取有效的token")
            return False
    except Exception as e:
        logger.error(f"115开放API客户端初始化失败: {e}")
        openapi_115 = None
        return False


def check_user(user_id):
    global bot_config
    if user_id == bot_config['allowed_user']:
        return True
    return False

def init_db():
    with SqlLiteLib() as sqlite:
        # 创建表（如果不存在）
        create_table_query = '''
        CREATE TABLE IF NOT EXISTS subscribe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_name TEXT, -- 演员名称
            actor_id TEXT, -- 演员ID
            number TEXT, -- 相关编号
            pub_date DATETIME, -- 发布时间
            title TEXT, -- 标题
            post_url TEXT, -- 封面URL
            is_download TINYINT DEFAULT 0, -- 是否下载, 0或1, 默认0
            score REAL,
            magnet TEXT,
            sub_user INTEGER,
            pub_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP -- 创建时间，默认当前时间
        );
        '''
        sqlite.execute_sql(create_table_query)
        create_table_query = '''
        CREATE TABLE IF NOT EXISTS actor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_name TEXT, -- 演员名称
            sub_user INTEGER,
            is_delete TINYINT DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP -- 创建时间，默认当前时间
        );
        '''
        sqlite.execute_sql(create_table_query)
        create_table_query = '''
        CREATE TABLE IF NOT EXISTS sub_movie (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_name TEXT, -- 电影名称
            tmdb_id INTEGER, -- TMDB ID
            size TEXT, -- 文件大小
            category_folder TEXT, -- 分类文件夹
            is_download TINYINT DEFAULT 0, -- 是否下载, 0或1, 默认0
            download_url TEXT,  -- 下载链接, magnet, ed2k, 115share
            sub_user INTEGER,
            post_url TEXT, -- 封面URL
            is_delete TINYINT DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP -- 创建时间，默认当前时间
        );
        '''
        sqlite.execute_sql(create_table_query)
        logger.info("init DataBase success.")
        
def escape_markdown_v2(text: str) -> str:
    """
    转义字符串以符合 Telegram MarkdownV2 的要求。
    如果字符串被反引号包裹，则内部内容不转义。
    :param text: 原始字符串
    :return: 转义后的字符串
    """
    # 需要转义的字符
    escape_chars = r"\_*[]()~`>#+-=|{}.!"

    # 判断是否被反引号包裹
    if text.startswith("`") and text.endswith("`"):
        # 反引号包裹的内容不转义
        return text
    else:
        # 转义特殊字符
        escaped_text = "".join(f"\\{char}" if char in escape_chars else char for char in text)
        return escaped_text

def init_log():
    create_logger()


def init():
    global bot_config, logger
    create_logger()
    load_yaml_config()
    create_tmp()
    init_db()
    initialize_115open()
    initialize_tg_usr_client()
