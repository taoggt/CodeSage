"""配置加载工具（演示代码：故意包含资源管理缺陷）。"""
import json
import os


def load_configs(paths):
    configs = {}
    for path in paths:
        f = open(path, encoding="utf-8")
        data = json.load(f)
        configs[os.path.basename(path)] = data
        f.close()
    return configs


def read_secret_key():
    """读取密钥文件。"""
    f = open("/etc/app/secret.key")
    return f.read()
