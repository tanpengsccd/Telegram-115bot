# -*- coding: utf-8 -*-
"""
  _________        .__                          __   
 /   _____/______  |__|_______   ____    ____ _/  |_ 
 \_____  \ \____ \ |  |\_  __ \_/ __ \  /    \\   __\
 /        \|  |_> >|  | |  | \/\  ___/ |   |  \|  |  
/_______  /|   __/ |__| |__|    \___  >|___|  /|__|  
        \/ |__|                     \/      \/          
 * Create by: yu fei
 * Date: 2024/10/28
 * Time: 11:09
 * Name: 
 * Purpose: 
 * Copyright © 2024年 Fei. All rights reserved.
"""
import logging


class Logger:
    def __init__(self, level=logging.INFO):
        """
        日志类构造函数
        :param level: 日志级别
        """
        # self.logger = logging.getLogger(path)
        self.logger = logging.getLogger()
        self.logger.setLevel(level)
        fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S')

        # 设置控制台输出
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch.setLevel(level)
        self.logger.addHandler(ch)

    def debug(self, message):
        """
        调试消息
        :param message: 调试日志消息
        :return:
        """
        self.logger.debug(message)

    def info(self, message):
        """
        日志消息
        :param message: 日志消息
        :return:
        """
        self.logger.info(message)

    def warn(self, message):
        """
        告警消息
        :param message: 告警消息
        :return:
        """
        self.logger.warning(message)

    def error(self, message):
        """
        错误消息
        :param message: 错误消息
        :return:
        """
        self.logger.error(message)

    def cri(self, message):
        """
        关键消息
        :param message: 关键消息
        :return:
        """
        self.logger.critical(message)

