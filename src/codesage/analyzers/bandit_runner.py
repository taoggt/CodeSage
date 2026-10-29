"""Bandit 集成：安全漏洞扫描（SQL 注入 / 命令注入 / 反序列化等）。

调用方式：bandit -f json -q <临时文件或目录>
（Bandit 不支持 stdin，写临时文件扫描；报告中的路径还原为原始相对路径）

性能设计（批量模式）：
Bandit 每次调用要付出解释器启动 + 依赖导入的成本（约 1.5s），
逐文件调用在 60+ 文件的仓库上会耗时 90 秒以上。
run_batch 把所有源码写入临时目录（保持相对路径结构），
一次 `bandit -r` 完成扫描——这是自举审查（用 CodeSage 扫自己）发现的优化点。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from codesage.analyzers.base import (
    Analyzer,
    find_tool,
    is_test_file,
    parse_json_output,
    run_command,
    tool_exists,
)
from codesage.core.models import Category, Finding, Severity, Source

_SEVERITY_MAP = {
    "HIGH": Severity.MAJOR,
    "MEDIUM": Severity.MINOR,
    "LOW": Severity.INFO,
}

_CONFIDENCE_MAP = {
    "HIGH": 0.95,
    "MEDIUM": 0.8,
    "LOW": 0.6,
}

# 按业务风险重映射的高危规则（对齐"工具视角"与"业务风险"）：
# 注入 / 任意代码执行 / 反序列化类漏洞即使被工具标为 MEDIUM，也应按高风险呈现——
# SQL 注入与"缺 docstring"同为 P2 是不可接受的呈现（用户反馈的真实案例）。
# 注：确定性层最高到 MAJOR（CRITICAL 留给 LLM 确认数据流可达性后升级提交）。
_ELEVATED_RULES: dict[str, Severity] = {
    "B608": Severity.MAJOR,  # SQL 注入（字符串构造查询）
    "B610": Severity.MAJOR,  # Django extra()/raw() SQL
    "B611": Severity.MAJOR,  # Django RawSQL
    "B307": Severity.MAJOR,  # eval()
    "B301": Severity.MAJOR,  # pickle 反序列化
    "B302": Severity.MAJOR,  # marshal 反序列化
    "B506": Severity.MAJOR,  # yaml.load（不安全加载）
}


def _severity_for(test_id: str, sev_key: str) -> Severity:
    """严重级映射：工具标注 + 高危规则按风险上调（只升不降）。"""
    mapped = _SEVERITY_MAP.get(sev_key, Severity.INFO)
    elevated = _ELEVATED_RULES.get(test_id)
    if elevated is not None and elevated.weight > mapped.weight:
        return elevated
    return mapped

# 测试代码中的惯例用法，不应视为安全问题（B101 是 Bandit 的经典噪音源）：
# - B101 assert_used：assert 在测试中是标准断言手段
# - B108 hardcoded_tmp_directory：测试 fixture 使用固定临时路径很常见
_TEST_FILE_SKIPS = {"B101", "B108"}


class BanditAnalyzer(Analyzer):
    name = "bandit"
    display = "Bandit（安全扫描）"
    supports_batch = True

    # 直扫真实目录时必须排除的目录（否则会递归进 .venv 等依赖目录）
    _EXCLUDE_GLOBS = (
        "*/venv/*,*/venv,*.venv/*,*/.venv,.venv,"
        "*/node_modules/*,*/node_modules,"
        "*/.git/*,*/.git,"
        "*/__pycache__/*,*/__pycache__,"
        "*/build/*,*/dist/*,*/.codesage/*,"
        "*/site-packages/*,*/.mypy_cache/*,*/.pytest_cache/*,*/.ruff_cache/*"
    )

    def is_available(self) -> bool:
        return tool_exists("bandit")

    def run(self, filename: str, source: str) -> list[Finding]:
        """单文件扫描（接口兼容；内部走批量通道）。"""
        return self.run_batch([(filename, source)]).get(filename, [])

    def run_batch(
        self,
        files: list[tuple[str, str]],
        base_dir: Path | None = None,
    ) -> dict[str, list[Finding]]:
        """批量扫描。

        base_dir 提供时直接扫描真实目录（零临时文件——Windows 下
        临时目录的写入与删除有显著开销，详见 RuffAnalyzer.run_batch 注释）；
        否则回退到临时目录模式。
        """
        results: dict[str, list[Finding]] = {name: [] for name, _ in files}
        exe = find_tool("bandit")
        if exe is None or not files:
            return results

        if base_dir is not None:
            return self._scan_dir(exe, Path(base_dir), results)

        with tempfile.TemporaryDirectory(prefix="codesage_bandit_") as tmp:
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
        """扫描目录；结果按已知文件清单过滤。"""
        proc = run_command(
            [
                exe,
                "-f",
                "json",
                "-q",
                "-r",
                str(scan_dir),
                "-x",
                self._EXCLUDE_GLOBS,
            ],
            timeout=300,
        )
        if proc is None:
            return results
        data = parse_json_output(proc.stdout)
        if not isinstance(data, dict):
            return results

        resolved_root = scan_dir.resolve()
        for item in data.get("results", []):
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
        """把扫描结果中的路径还原为原始相对路径。"""
        if not raw:
            return None
        try:
            rel = Path(raw).resolve().relative_to(resolved_root).as_posix()
            if rel in known:
                return rel
        except (ValueError, OSError):
            pass
        # 回退：按文件名匹配（应对临时目录被符号链接等边缘情况）
        base = Path(raw).name
        for name in known:
            if Path(name).name == base:
                return name
        return None

    @staticmethod
    def _to_finding(item: dict, filename: str) -> Finding | None:
        """转换结果；测试文件中的惯例用法被过滤（返回 None）。"""
        test_id = str(item.get("test_id", ""))
        if test_id in _TEST_FILE_SKIPS and is_test_file(filename):
            return None
        sev_key = str(item.get("issue_severity", "LOW")).upper()
        conf_key = str(item.get("issue_confidence", "LOW")).upper()
        line = int(item.get("line_number", 1) or 1)
        return Finding(
            file=filename,
            line_start=line,
            line_end=line,
            category=Category.SECURITY,
            severity=_severity_for(test_id, sev_key),
            title=f"[{test_id}] {item.get('test_name', '')}",
            description=str(item.get("issue_text", "")).strip(),
            suggestion="请查阅 Bandit 文档对应规则的安全修复建议",
            evidence=str(item.get("code", "")).strip()[:200],
            confidence=_CONFIDENCE_MAP.get(conf_key, 0.6),
            source=Source.BANDIT,
            rule_id=test_id,
        )


# 兼容旧接口：单文件临时扫描（保留供独立调试使用）
def scan_single_file(exe: str, source: str) -> str | None:
    """把源码写入临时文件并返回 Bandit 的 JSON 输出（调试辅助）。"""
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(source)
            tmp_path = f.name
        proc = run_command([exe, "-f", "json", "-q", tmp_path], timeout=90)
        return proc.stdout if proc else None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
