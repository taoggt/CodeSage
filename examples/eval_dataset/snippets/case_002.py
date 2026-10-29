"""配置加载（评测案例：资源泄漏与可变默认参数）。"""
import json


def load_configs(paths, cache=[]):
    """批量加载配置（带缓存）。"""
    for path in paths:
        if path in cache:
            continue
        f = open(path, encoding="utf-8")
        data = json.load(f)
        cache.append(path)
        yield data
        f.close()
