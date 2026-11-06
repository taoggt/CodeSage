"""Ruff 集成：代码风格 / 命名规范 / 导入顺序 / 潜在错误。

两种调用模式：
- 单文件（stdin）：源码不需要落盘，接口通用；
- 批量（临时目录 + 目录扫描）：一次调用处理全部文件——
  大仓库下比逐文件模式快一个数量级（与 BanditAnalyzer 同一优化思路）。

规则集选择说明：
- E/W：PEP8 风格
- F：Pyflakes（未定义名、未使用导入等，含真实 bug 信号）
- I：isort 导入排序
- N：pep8-naming 命名规范
- 不选 D（pydocstyle）：docstring 完整性由自研 AST 规则负责，避免重复报告
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from codesage.analyzers.base import (
    Analyzer,
    find_tool,
    parse_json_output,
    run_command,
    tool_exists,
)
from codesage.core.languages import Language
from codesage.core.models import Category, Finding, Severity, Source

# F 规则中代表明确逻辑问题的子集（提升严重级）
_SEVERE_F_CODES = {
    "F821",  # undefined name
    "F811",  # redefinition of unused name
    "F823",  # local variable referenced before assignment
    "F502",  # expected list, got something else
    "F522",  # string formatting issues
    "F706",  # return outside function
    "F707",  # except after bare except
    "F901",  # raise NotImplemented
}


class RuffAnalyzer(Analyzer):
    name = "ruff"
    display = "Ruff（风格 / 命名 / 导入）"
    supports_batch = True
    # Ruff 只解析 Python：混合仓库里必须靠这个声明把它挡在 .java 之外
    languages = (Language.PYTHON,)
    select = "E,W,F,I,N"

    def is_available(self) -> bool:
        return tool_exists("ruff")

    # ------------------------------------------------------------ 单文件模式

    def run(self, filename: str, source: str) -> list[Finding]:
        exe = find_tool("ruff")
        if exe is None:
            return []
        proc = run_command(
            [
                exe,
                "check",
                "--output-format=json",
                f"--select={self.select}",
                "--stdin-filename",
                filename,
                "-",
            ],
            stdin_text=source,
            timeout=60,
        )
        if proc is None:
            return []
        data = parse_json_output(proc.stdout)
        if not isinstance(data, list):
            return []
        findings: list[Finding] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            finding = self._to_finding(item, filename)
            if finding is not None:
                findings.append(finding)
        return findings

    # ------------------------------------------------------------ 批量模式

    def run_batch(
        self,
        files: list[tuple[str, str]],
        base_dir: Path | None = None,
    ) -> dict[str, list[Finding]]:
        """批量扫描。

        性能设计（cProfile 实测的优化结论）：
        - 临时目录模式在 Windows 下有隐藏成本：删除 62 个文件实测 6.7s
          （nt.remove/nt.rmdir，杀毒软件与文件系统挂钩导致）；
        - 因此提供 base_dir 参数：源码本来就在磁盘上时（pipeline 与 eval
          的全部真实场景），直接扫描真实目录——零临时文件、零清理成本。
        临时目录模式仅作兜底（调用方仅有内存源码时）。
        """
        results: dict[str, list[Finding]] = {name: [] for name, _ in files}
        exe = find_tool("ruff")
        if exe is None or not files:
            return results

        if base_dir is not None:
            return self._scan_dir(exe, Path(base_dir), results)

        with tempfile.TemporaryDirectory(prefix="codesage_ruff_") as tmp:
            tmp_path = Path(tmp)
            for name, source in files:
                target = tmp_path / name
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source, encoding="utf-8")
                except OSError:
                    continue
            return self._scan_dir(exe, tmp_path, results)

    def _scan_dir(
        self, exe: str, scan_dir: Path, results: dict[str, list[Finding]]
    ) -> dict[str, list[Finding]]:
        """扫描一个目录并把结果按已知文件过滤（不在清单中的结果被忽略）。"""
        proc = run_command(
            [
                exe,
                "check",
                "--output-format=json",
                f"--select={self.select}",
                str(scan_dir),
            ],
            timeout=120,
        )
        if proc is None:
            return results
        data = parse_json_output(proc.stdout)
        if not isinstance(data, list):
            return results

        resolved_root = scan_dir.resolve()
        for item in data:
            if not isinstance(item, dict):
                continue
            rel = self._to_relative(str(item.get("filename", "")), resolved_root, results)
            if rel is None:
                continue
            finding = self._to_finding(item, rel)
            if finding is not None:
                results[rel].append(finding)
        return results

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _to_relative(raw: str, resolved_root: Path, known: dict) -> str | None:
        if not raw:
            return None
        try:
            rel = Path(raw).resolve().relative_to(resolved_root).as_posix()
            if rel in known:
                return rel
        except (ValueError, OSError):
            pass
        base = Path(raw).name
        for name in known:
            if Path(name).name == base:
                return name
        return None

    def _to_finding(self, item: dict, filename: str) -> Finding | None:
        code = str(item.get("code", ""))
        if not code:
            return None  # 语法错误等无 code 的条目交给自研引擎报告
        message = str(item.get("message", ""))
        location = item.get("location") or {}
        end_location = item.get("end_location") or {}
        row = int(location.get("row", 1))
        end_row = int(end_location.get("row", row))
        severity, category = self._classify(code)
        return Finding(
            file=filename,
            line_start=row,
            line_end=max(row, end_row),
            category=category,
            severity=severity,
            title=f"[{code}] {message}",
            description=f"Ruff 规则 {code}：{message}",
            suggestion="运行 `ruff check --fix` 可自动修复部分问题",
            confidence=0.9,
            source=Source.RUFF,
            rule_id=code,
        )

    @staticmethod
    def _classify(code: str) -> tuple[Severity, Category]:
        prefix = code[0] if code else ""
        if prefix == "F":
            if code in _SEVERE_F_CODES:
                return Severity.MAJOR, Category.BUG
            return Severity.MINOR, Category.QUALITY
        if prefix in ("E", "W"):
            return Severity.MINOR, Category.QUALITY
        if prefix in ("N", "I"):
            return Severity.INFO, Category.QUALITY
        return Severity.INFO, Category.QUALITY
