"""报表生成（评测案例：循环内字符串拼接）。"""


def build_report(rows):
    """把数据行拼成文本报表。"""
    text = ""
    for row in rows:
        text += f"{row['name']}: {row['value']}\n"
    return text
