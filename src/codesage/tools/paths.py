"""工作区路径守卫：所有接收「模型提供的路径」的工具共用同一套规则。

为什么单独成模块：read_file / submit_finding / dismiss_candidate 各自解析一次路径，
迟早会出现三套不一致的判定——只要有一处漏了越权检查，模型提交的路径就能
越过工作区边界。这里把「解析 + 越权判断 + 规范化」收敛成唯一真相源。

规则（与 read_file 原有行为保持一致）：
- 路径按工作区根目录解析，resolve() 展开 .. 与符号链接后再判定归属；
- 越界、绝对路径、非法路径一律返回 None，由调用方回 ERROR 文本；
- 成功时返回规范化的 POSIX 相对路径（去掉 ./ 前缀），保证去重键稳定。
"""

from __future__ import annotations

from pathlib import Path


def resolve_inside(root: Path, path: str) -> Path | None:
    """把相对路径解析到工作区内；越权或非法路径返回 None。"""
    try:
        target = (root / path).resolve()
    except (OSError, ValueError, TypeError):
        return None
    try:
        if not target.is_relative_to(root.resolve()):
            return None
    except ValueError:
        return None
    return target


def normalize_relative(target: Path, root: Path) -> str:
    """转成相对工作区的 POSIX 路径（用于落库与去重键）。"""
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return target.name
