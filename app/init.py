# -*- coding: utf-8 -*-

import os
import yaml
from logger import Logger
from typing import Optional
from sqlitelib import *


# 全局日志
logger:Optional[Logger] = None

# 全局配置
bot_config = dict()

# 115客户端对象
client_115 = None

# yaml配置文件目录
CONFIG_FILE = "/config/config.yaml"

# 115Cookie File
COOKIE_FILE = "/config/cookie.txt"

# DB File
DB_FILE = "/config/db.db"

JAVDB_COOKIE = ""
USER_AGENT = ""

# 调试用
# CONFIG_FILE = "config/config.yaml"
# COOKIE_FILE = "config/cookie.txt"
# DB_FILE = "config/db.db"


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
    if not os.path.exists("tmp"):
        os.mkdir("tmp")

def initialize_115client():
    global client_115
    from client_115 import Client_115
    client_115 = Client_115()
    if client_115.client is None:
        return False
    else:
        return True


def check_user(user_id):
    global bot_config
    if user_id in bot_config['allowed_user_list']:
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

def init_log():
    create_logger()


def init():
    global bot_config, logger
    create_logger()
    load_yaml_config()
    create_tmp()
    init_db()
