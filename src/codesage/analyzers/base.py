"""分析器基类与子进程调用工具。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from codesage.core.models import Finding


class Analyzer(ABC):
    """确定性分析器协议。"""

    name: str = "analyzer"
    display: str = "分析器"
    supports_batch: bool = False  # True 时 pipeline 优先调用 run_batch（性能路径）

    @abstractmethod
    def is_available(self) -> bool:
        """工具是否可用（未安装时优雅跳过，不阻塞审查）。"""

    @abstractmethod
    def run(self, filename: str, source: str) -> list[Finding]:
        """分析源码，返回发现列表。filename 为相对路径（用于报告中显示）。"""


def find_tool(executable: str) -> str | None:
    """查找外部命令行工具，返回可执行文件路径。

    查找顺序（关键工程细节）：
    1. 当前解释器所在目录（venv/Scripts）——保证用项目声明的依赖版本，
       避免被系统全局安装的旧版本干扰（版本差异会改变输出格式）；
    2. PATH 环境变量 —— 回退到全局安装的工具。

    为什么需要第 1 步：不激活 venv 直接运行 codesage.exe 时，
    PATH 中不包含 venv/Scripts，Bandit 这类仅装在 venv 里的工具会找不到。
    """
    scripts_dir = Path(sys.executable).parent
    for name in (executable, f"{executable}.exe", f"{executable}.cmd"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(executable)


def tool_exists(executable: str) -> bool:
    """检查外部命令行工具是否可用。"""
    return find_tool(executable) is not None


_TEST_FILE_PREFIXES = ("test_", "conftest")
_TEST_FILE_SUFFIX = "_test.py"
_TEST_DIR_NAMES = {"test", "tests"}


def is_test_file(path: str) -> bool:
    """判断路径是否属于测试代码（误报控制的关键基础设施）。

    识别规则（pytest 生态的常见约定）：
    - 目录：路径中出现 test/ 或 tests/ 目录；
    - 文件名：test_*.py / *_test.py / conftest.py。

    为什么需要它：测试代码有其领域惯例——大量使用 assert、
    函数不写 docstring、fixture 中硬编码路径——这些在测试语境下
    是标准做法，报告它们属于典型误报（会淹没真实问题）。
    """
    normalized = path.replace("\\", "/")
    p = Path(normalized)
    name = p.name
    if name.startswith(_TEST_FILE_PREFIXES) or name.endswith(_TEST_FILE_SUFFIX):
        return True
    return any(part in _TEST_DIR_NAMES for part in p.parts[:-1])


def run_command(
    cmd: list[str],
    stdin_text: str | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str] | None:
    """执行子进程，返回结果；任何异常返回 None（调用方降级处理）。"""
    try:
        return subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_json_output(text: str) -> Any | None:
    """容错解析 JSON 输出。"""
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
