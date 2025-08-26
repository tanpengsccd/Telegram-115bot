#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from telethon import TelegramClient
import os

"""
独立的Telegram Session文件创建脚本
使用方法：
1. 修改下面的 API_ID 和 API_HASH
2. 运行脚本：python create_tg_session_file.py
3. 按照提示输入手机号和验证码
4. 将生成的 user_session.session 文件放到 config 目录
"""

# 请在这里填入你的API配置
# 获取地址：https://my.telegram.org/auth
API_ID = 'your_api_id'  # 替换为你的API ID
API_HASH = 'your_api_hash'  # 替换为你的API Hash


if os.path.exists('user_session.session'):
    print("Session file already exists.")
else:
    client = TelegramClient('user_session', API_ID, API_HASH)
    if not os.path.exists('user_session.session'):
        print("Session file created failed.")
    else:
        print("Session file created successfully.")
        print("Session file path:", os.path.abspath('user_session.session'))