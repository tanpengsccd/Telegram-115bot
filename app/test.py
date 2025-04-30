from sqlitelib import *


if __name__ == '__main__':
    sqlite = SqlLiteLib()
    sqlite.connect("config/db.db")
    print("ok")