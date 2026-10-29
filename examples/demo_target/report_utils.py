"""报表工具（演示代码：故意包含性能反模式）。"""


def build_text_report(rows):
    text = ""
    for row in rows:
        text += f"{row['name']}: {row['value']}\n"
    return text


def filter_active(items):
    """过滤出活跃状态的项目。"""
    result = []
    for item in items:
        if item["status"] in ["active", "pending", "processing"]:
            result.append(item)
    return result
