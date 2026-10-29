"""用户服务模块（评测案例：SQL 注入）。"""
import sqlite3


def get_user(username: str):
    """按用户名查询用户记录。"""
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = f"SELECT id, name FROM users WHERE name = '{username}'"
    cursor.execute(query)
    return cursor.fetchone()
