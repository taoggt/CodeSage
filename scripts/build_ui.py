#!/usr/bin/env python
"""构建前端（web/）到 src/codesage/server/static/。

用法：
    python scripts/build_ui.py            # 装依赖（首次）+ 构建
    python scripts/build_ui.py --skip-install

设计说明：
- 前端产物不入库（见 .gitignore），但会通过 hatch 的 force-include 打进 wheel；
- 没有 Node 时给出明确报错，而不是抛一堆栈；
- 只做"构建"这一件事，不负责启动服务。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STATIC = ROOT / "src" / "codesage" / "server" / "static"


def run(command: list[str], cwd: Path) -> None:
    printable = " ".join(command)
    print(f"$ {printable}")
    try:
        subprocess.run(command, cwd=str(cwd), check=True)
    except OSError:
        # Windows 下 npm 是 .cmd，某些环境需要 shell 才能执行
        subprocess.run(printable, cwd=str(cwd), check=True, shell=True)


def main(argv: list[str]) -> int:
    if not WEB.is_dir():
        print(f"[x] 找不到前端工程目录：{WEB}", file=sys.stderr)
        return 2

    npm = shutil.which("npm")
    if npm is None:
        print(
            "[x] 未检测到 Node.js / npm。\n"
            "    请安装 Node 18+ 后重试：https://nodejs.org/\n"
            "    若只想用后端 API：codesage ui 仍可启动，页面会提示需要构建。",
            file=sys.stderr,
        )
        return 2

    skip_install = "--skip-install" in argv
    if not skip_install and not (WEB / "node_modules").is_dir():
        lock = WEB / "package-lock.json"
        run([npm, "ci" if lock.is_file() else "install"], WEB)

    run([npm, "run", "build"], WEB)

    index = STATIC / "index.html"
    if not index.is_file():
        print(f"[x] 构建结束但没有产物：{index}", file=sys.stderr)
        return 1

    print(f"\n[ok] 前端已构建：{STATIC}")
    print("     启动：codesage ui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
