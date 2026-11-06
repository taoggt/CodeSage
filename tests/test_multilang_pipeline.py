"""多语言链路的集成测试：Prompt / 简报 / 导出围栏 / 自动探测。

单元测试各自锁住一个环节，这里验证它们串起来仍然正确——
因为多语言改造的最大风险不是"某个函数写错"，而是"各层都以为自己对了，
合起来报告里却混进了错误语言的发现"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codesage.agent.context import build_task_brief
from codesage.agent.profiles import build_system_prompt
from codesage.cli.exporter import markdown_fence, to_markdown
from codesage.core.config import ReviewSettings, Settings
from codesage.core.languages import Language
from codesage.core.models import Category, Finding, ReviewReport, Severity, Source
from codesage.pipeline import ReviewOptions, run_review

JAVA_SOURCE = """public class Demo {
    public void run(String id) {
        try {
            fetch(id);
        } catch (RuntimeException e) {
        }
    }
}
"""

PYTHON_SOURCE = "def collect(items=[]):\n    return items\n"


def make_settings(tmp_path: Path, languages: str = "auto") -> Settings:
    return Settings(
        review=ReviewSettings(
            store_path=str(tmp_path / ".test_store.db"), languages=languages
        )
    )


# ---------------------------------------------------------------- Prompt


def test_prompt_defaults_to_python_wording():
    prompt = build_system_prompt("full")
    assert "资深 Python 代码审查专家" in prompt
    assert "Pythonic" in prompt


def test_prompt_switches_role_for_java():
    prompt = build_system_prompt("full", languages=[Language.JAVA])
    assert "资深 Java 代码审查专家" in prompt
    assert "src/test/java" in prompt
    assert "Effective Java" in prompt
    # Java 独有的维度必须出现，Python 的人设必须消失
    assert "@Transactional" in prompt
    assert "资深 Python 代码审查专家" not in prompt


def test_prompt_handles_mixed_languages():
    prompt = build_system_prompt("full", languages=[Language.PYTHON, Language.JAVA])
    assert "资深代码审查专家（Python / Java）" in prompt
    assert "（Python）" in prompt and "（Java）" in prompt


# ---------------------------------------------------------------- 任务简报


def test_task_brief_single_language_matches_legacy():
    brief = build_task_brief(
        target="/p",
        files=[("a.py", 3)],
        candidates=[],
        languages={"python": 1},
    )
    assert "Python 文件 1 个" in brief


def test_task_brief_mixed_languages():
    brief = build_task_brief(
        target="/p",
        files=[("a.py", 3), ("A.java", 9)],
        candidates=[],
        languages={"python": 1, "java": 1},
    )
    assert "文件 2 个（Python 1 个、Java 1 个）" in brief


# ---------------------------------------------------------------- 导出围栏


def test_markdown_fence_per_language():
    assert markdown_fence("python") == "python"
    assert markdown_fence("java") == "java"
    assert markdown_fence("kotlin") == "text"   # 未注册语言不猜


def test_markdown_export_uses_finding_language_fence():
    report = ReviewReport(target="/p/A.java")
    report.findings = [
        Finding(
            file="A.java",
            line_start=2,
            line_end=2,
            category=Category.BUG,
            severity=Severity.MAJOR,
            title="空 catch",
            evidence="catch (RuntimeException e) { }",
            source=Source.JAVA_RULE,
            rule_id="JG001",
            language="java",
        )
    ]
    text = to_markdown(report)
    assert "```java" in text
    assert "```python" not in text


# ---------------------------------------------------------------- 管线探测


def test_auto_mode_reviews_both_languages(tmp_path: Path):
    """混合仓库：一次审查同时产出两种语言的发现，且来源互不串味。"""
    pytest.importorskip("tree_sitter_java", reason='需要 pip install -e ".[java]"')

    (tmp_path / "app.py").write_text(PYTHON_SOURCE, encoding="utf-8")
    (tmp_path / "Demo.java").write_text(JAVA_SOURCE, encoding="utf-8")

    report = run_review(
        tmp_path,
        make_settings(tmp_path),
        ReviewOptions(use_llm=False, persist=False),
    )

    assert report.metadata["language_mode"] == "auto"
    assert report.metadata["languages"] == {"python": 1, "java": 1}

    sources_by_file = {
        f.file: {x.source for x in report.findings if x.file == f.file}
        for f in report.findings
    }
    assert Source.JAVA_RULE in sources_by_file["Demo.java"]
    assert Source.JAVA_RULE not in sources_by_file["app.py"]
    # Java 文件绝不能被 Python 引擎判成"语法错误"
    assert all(
        not (f.file == "Demo.java" and f.rule_id == "CG000") for f in report.findings
    )


def test_language_scope_filters_files(tmp_path: Path):
    """--language python 时 .java 不该进入审查范围。"""
    (tmp_path / "app.py").write_text(PYTHON_SOURCE, encoding="utf-8")
    (tmp_path / "Demo.java").write_text(JAVA_SOURCE, encoding="utf-8")

    report = run_review(
        tmp_path,
        make_settings(tmp_path),
        ReviewOptions(use_llm=False, persist=False, languages="python"),
    )
    assert report.metadata["languages"] == {"python": 1}
    assert all(f.file.endswith(".py") for f in report.findings)


def test_empty_scope_error_mentions_language(tmp_path: Path):
    """限定语言但目录里没有该语言文件时，报错要说清原因。"""
    (tmp_path / "Demo.java").write_text(JAVA_SOURCE, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        run_review(
            tmp_path,
            make_settings(tmp_path),
            ReviewOptions(use_llm=False, persist=False, languages="python"),
        )
    assert "python" in str(exc.value)
    assert ".py" in str(exc.value)
