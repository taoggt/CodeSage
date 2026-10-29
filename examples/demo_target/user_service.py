"""用户查询服务（演示代码：故意包含安全缺陷，请勿模仿）。"""
import sqlite3

# 演示：硬编码的生产密钥（应改为环境变量或密钥管理服务）
DB_PASSWORD = "prod-pass-9527"
API_TOKEN = "sk-live-8f3a2b1c9d"


def get_user_by_name(username):
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = f"SELECT id, name, email FROM users WHERE name = '{username}'"
    cursor.execute(query)
    row = cursor.fetchone()
    conn.close()
    return row


def search_users(keyword, limit=10):
    """按关键字模糊查询用户。"""
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    sql = "SELECT id, name FROM users WHERE name LIKE '%" + keyword + "%' LIMIT " + str(limit)
    cursor.execute(sql)
    return cursor.fetchall()
