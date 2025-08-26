# -*- coding: utf-8 -*-

import sqlite3
import init
from typing import Optional


class SqlLiteLib:
    def __init__(self):
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None
        self.logger = init.logger

    def __enter__(self):
        self.connect(init.DB_FILE)  # 自动连接
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()  # 自动关闭连接

    def connect(self, db_file:str):
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()

    def execute_sql(self, sql: str, params: tuple = ()):
        try:
            self.cursor.execute(sql, params)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            self.logger.error(f"执行查询时发生错误: {e}, sql: {sql}")

    def query(self, sql: str, params: tuple = ()):
        self.cursor.execute(sql, params)
        res_list = self.cursor.fetchall()
        return res_list
    
    def query_all(self, sql: str, params: tuple = ()):
        """查询所有记录，返回字典列表"""
        try:
            self.cursor.execute(sql, params)
            columns = [description[0] for description in self.cursor.description]
            rows = self.cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            self.logger.error(f"执行查询时发生错误: {e}, sql: {sql}")
            return []
    
    def query_one(self, sql: str, params=None):
        try:
            self.cursor.execute(sql, params or ())
            res = self.cursor.fetchone()
            return res[0] if res else None
        except Exception as e:
            self.logger.error(f"执行查询时发生错误: {e}, sql: {sql}")
            return None
            
    def query_row(self, sql: str, params=None):
        try:
            self.cursor.execute(sql, params or ())
            res = self.cursor.fetchone()
            return res if res else None
        except Exception as e:
            self.logger.error(f"执行查询时发生错误: {e}, sql: {sql}")

    def close(self):
        if self.cursor is not None:
            self.cursor.close()
        if self.conn is not None:
            self.conn.close()
