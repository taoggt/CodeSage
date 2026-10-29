"""数据同步（演示代码：故意包含复杂函数与过宽异常捕获）。"""
import logging

logger = logging.getLogger(__name__)


def normalize_record(record, strict=False, defaults=None):
    """把外部数据规范化为内部结构。"""
    if record is None:
        return {}
    normalized = {}
    if "id" in record:
        normalized["id"] = str(record["id"])
    elif strict:
        raise ValueError("缺少 id")
    if "name" in record:
        name = record["name"] if record["name"] is not None else ""
        if len(name) > 128:
            name = name[:128]
        normalized["name"] = name.strip()
    elif defaults and "name" in defaults:
        normalized["name"] = defaults["name"]
    if "email" in record:
        email = record["email"]
        if email and "@" in email:
            normalized["email"] = email.lower()
        elif strict:
            raise ValueError("email 格式非法")
    if "tags" in record:
        tags = record["tags"]
        if isinstance(tags, (list, tuple)):
            normalized["tags"] = [str(t) for t in tags if t]
        elif tags:
            normalized["tags"] = [str(tags)]
    return normalized


def sync_all(records):
    """批量同步记录。"""
    synced = []
    for record in records:
        try:
            synced.append(normalize_record(record, strict=True))
        except Exception:
            logger.exception("同步失败")
    return synced
