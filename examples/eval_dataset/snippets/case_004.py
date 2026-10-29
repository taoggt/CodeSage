"""旧版工具函数（评测案例：质量维度）。"""
import os


def read_lines(path):
    try:
        f = open(path)
        return f.readlines()
    except:
        return []


def is_empty(value):
    if value == None:
        return True
    return len(value) == 0
