"""运维脚本（演示代码：故意包含命令注入与代码执行风险）。"""
import os
import subprocess


def run_backup(target_dir):
    """备份指定目录到当前路径。"""
    command = "tar -czf backup.tar.gz " + target_dir
    os.system(command)


def check_disk(host):
    subprocess.run(f"ssh {host} df -h", shell=True)


def convert_currency(amount, rate_expression):
    result = eval(rate_expression)
    return amount * result
