"""文件处理（评测案例：命令注入）。"""
import subprocess


def convert_image(user_path: str):
    """调用 ImageMagick 转换图片。"""
    cmd = f"convert {user_path} out.png"
    subprocess.run(cmd, shell=True)
