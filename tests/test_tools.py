"""工具层测试：文本归一化、事实校验、去重与驳回约束。

背景：真实使用中发现部分模型通过 function calling 提交多行文本时
会双重转义——JSON 解析后剩下字面 "\n" 序列（显示为一串 \n），
本文件锁定归一化修复的行为（含防止误伤代码字面量的保守策略）。

2026-11 审计后新增：submit_finding 的路径/行号/引文校验、category 无关的
判重、同位置不同问题并报、dismiss_candidate 的候选存在性校验、read_file
的已读文件台账。所有用例都基于 tmp_path 里的真实文件，不再用 Path(".")。
"""

from pathlib import Path

from codesage.core.models import Category, Finding, Severity, Source
from codesage.tools.dismiss_candidate import make_dismiss_candidate_tool
from codesage.tools.read_file import make_read_file_tool
from codesage.tools.submit_finding import (
    UNVERIFIED_CONFIDENCE_CAP,
    make_submit_finding_tool,
)

SOURCE = (
    "import subprocess\n"
    "\n"
    "def run(cmd):\n"
    "    return subprocess.call(cmd, shell=True)\n"
    "\n"
    "def unsafe(user_input):\n"
    "    return run('echo ' + user_input)\n"
    "\n"
    "def empty():\n"
    "    return None\n"
)
EVIDENCE = "    return subprocess.call(cmd, shell=True)"


def make_tool(tmp_path: Path):
    (tmp_path / "a.py").write_text(SOURCE, encoding="utf-8")
    collector: list[Finding] = []
    tool = make_submit_finding_tool(collector, tmp_path)
    return collector, tool


def submit(tool, **overrides):
    """调用工具函数（make_submit_finding_tool 返回的是被 @tool 标记的函数）。"""
    params = dict(
        file="a.py",
        line_start=1,
        line_end=2,
        category="security",
        severity="major",
        title="测试发现",
    )
    params.update(overrides)
    return tool(**params)


# ---------------------------------------------------------------- 文本归一化


def test_double_escaped_newlines_normalized(tmp_path: Path):
    """模型双重转义（字面反斜杠+n）应被还原为真实换行。"""
    collector, tool = make_tool(tmp_path)
    submit(
        tool,
        suggestion="改用参数列表形式：\\n\\n    import subprocess\\n    run()",
    )
    suggestion = collector[0].suggestion
    assert "\n" in suggestion          # 已还原为真实换行
    assert "\\n" not in suggestion     # 不再有字面序列
    assert "    import subprocess" in suggestion


def test_real_newlines_untouched(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    submit(tool, suggestion="正常第一行\n正常第二行")
    assert collector[0].suggestion == "正常第一行\n正常第二行"


def test_single_escape_literal_kept(tmp_path: Path):
    """单个字面 \\n 通常是代码里的字符串字面量，不应被误还原。"""
    collector, tool = make_tool(tmp_path)
    submit(tool, suggestion='print("a\\nb") 的正确用法')
    assert "\\n" in collector[0].suggestion   # 保留字面
    assert "\n" not in collector[0].suggestion


def test_description_also_normalized(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    submit(tool, description="触发条件如下：\\n第一段\\n第二段")
    assert "\n" in collector[0].description


def test_invalid_enum_returns_error_text(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    output = submit(tool, category="not_a_category")
    assert output.startswith("ERROR")
    assert not collector


# ---------------------------------------------------------------- 事实校验


def test_hallucinated_path_rejected(tmp_path: Path):
    """不存在的文件路径必须打回，不能照单全收。"""
    collector, tool = make_tool(tmp_path)
    output = submit(tool, file="ghost.py")
    assert output.startswith("ERROR")
    assert "文件不存在" in output
    assert not collector


def test_out_of_range_line_rejected(tmp_path: Path):
    """a.py 只有 10 行，a.py:999 必须打回并附上真实行数。"""
    collector, tool = make_tool(tmp_path)
    output = submit(tool, line_start=999, line_end=999)
    assert output.startswith("ERROR")
    assert "总行数（10）" in output
    assert not collector


def test_line_end_clamped_to_file_end(tmp_path: Path):
    """结束行超界不阻断提交，但收敛到末行并告知模型。"""
    collector, tool = make_tool(tmp_path)
    output = submit(tool, line_end=500)
    assert collector[0].line_end == 10
    assert "收敛到文件末行 10" in output


def test_path_traversal_rejected(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    assert submit(tool, file="../../etc/passwd").startswith("ERROR")
    assert not collector


def test_absolute_path_outside_workspace_rejected(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    output = submit(tool, file="/etc/passwd")
    assert output.startswith("ERROR")
    assert not collector


def test_leading_dot_slash_normalized(tmp_path: Path):
    """./a.py 与 a.py 必须落到同一个 file 值，否则去重键会分叉。"""
    collector, tool = make_tool(tmp_path)
    submit(tool, file="./a.py")
    assert collector[0].file == "a.py"


def test_matching_evidence_keeps_high_confidence(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    submit(tool, line_start=4, line_end=4, evidence=EVIDENCE, confidence=0.95)
    assert collector[0].confidence == 0.95
    assert collector[0].evidence == EVIDENCE


def test_fabricated_evidence_rejected_with_real_lines(tmp_path: Path):
    """编造的引文必须打回，并把真实代码回给模型供其纠正。"""
    collector, tool = make_tool(tmp_path)
    output = submit(
        tool,
        line_start=4,
        line_end=4,
        evidence="return sanitize(user_input)",
    )
    assert output.startswith("ERROR")
    assert "evidence" in output
    assert "subprocess.call" in output       # 回喂了真实代码
    assert not collector


def test_missing_evidence_caps_confidence(tmp_path: Path):
    """不提供引文不阻断流程，但置信度封顶，让无证据结论可识别。"""
    collector, tool = make_tool(tmp_path)
    output = submit(tool, confidence=0.99)
    assert collector[0].confidence == UNVERIFIED_CONFIDENCE_CAP
    assert "置信度封顶" in output


# ---------------------------------------------------------------- 去重语义


def test_dedup_same_location_keeps_higher_confidence(tmp_path: Path):
    collector, tool = make_tool(tmp_path)
    # 同一条问题（标题相同）重复提交时，按置信度保留更好的版本
    submit(tool, confidence=0.6, title="命令注入")
    submit(tool, confidence=0.95, title="命令注入")
    assert len(collector) == 1
    assert collector[0].title == "命令注入"
    assert collector[0].confidence == UNVERIFIED_CONFIDENCE_CAP


def test_same_issue_with_different_category_is_deduped(tmp_path: Path):
    """旧键含 category，换个类别就绕过判重；现在必须仍被判为同一条。"""
    collector, tool = make_tool(tmp_path)
    submit(tool, line_start=4, line_end=4, category="bug", title="命令注入", evidence=EVIDENCE)
    output = submit(
        tool,
        line_start=4,
        line_end=4,
        category="security",
        title="命令注入",
        evidence=EVIDENCE,
        confidence=0.99,
    )
    assert len(collector) == 1
    assert "已更新" in output or "忽略本条" in output


def test_different_issues_same_location_both_kept(tmp_path: Path):
    """同桶同类别但确为两个问题，不能静默吞掉第二个。"""
    collector, tool = make_tool(tmp_path)
    submit(tool, line_start=6, line_end=6, category="bug", title="空指针解引用")
    output = submit(tool, line_start=7, line_end=7, category="bug", title="资源未释放")
    assert len(collector) == 2
    assert "空指针解引用" in output      # 已有标题回喂给模型，供其自纠
    assert "请勿重复提交" in output


def test_adjacent_lines_across_old_bucket_are_nearby(tmp_path: Path):
    """旧版 line//3 分桶让第 2、3 行相邻却不同桶；现在统一按邻接处理。"""
    collector, tool = make_tool(tmp_path)
    submit(tool, line_start=2, line_end=2, category="bug", title="问题一")
    output = submit(tool, line_start=3, line_end=3, category="bug", title="问题二")
    assert len(collector) == 2
    assert "问题一" in output


# ---------------------------------------------------------------- 台账与驳回


def test_read_file_records_visit_log(tmp_path: Path):
    (tmp_path / "a.py").write_text(SOURCE, encoding="utf-8")
    log: list[str] = []
    tool = make_read_file_tool(tmp_path, visit_log=log)
    assert "subprocess.call" in tool(path="a.py")
    assert log == ["a.py"]
    assert tool(path="../outside.py").startswith("ERROR")
    assert log == ["a.py"]      # 失败的读取不记台账


def test_dismiss_rejects_unknown_candidate(tmp_path: Path):
    """dismiss 会删报告条目，不能让模型按任意位置驳回。"""
    (tmp_path / "a.py").write_text(SOURCE, encoding="utf-8")
    candidate = Finding(
        file="a.py",
        line_start=4,
        line_end=4,
        category=Category.SECURITY,
        severity=Severity.MAJOR,
        title="命令注入",
        source=Source.AST_RULE,
        rule_id="CG001",
    )
    dismissed: list[dict] = []
    tool = make_dismiss_candidate_tool(
        dismissed, workspace=tmp_path, candidates=[candidate]
    )
    assert tool(file="a.py", line_start=8, reason="误报").startswith("ERROR")
    assert not dismissed
    assert tool(file="a.py", line_start=4, reason="误报").startswith("已驳回")
    assert dismissed[0]["file"] == "a.py"


def test_dismiss_rejects_hallucinated_path(tmp_path: Path):
    (tmp_path / "a.py").write_text(SOURCE, encoding="utf-8")
    dismissed: list[dict] = []
    tool = make_dismiss_candidate_tool(dismissed, workspace=tmp_path, candidates=[])
    assert tool(file="ghost.py", line_start=1).startswith("ERROR")
    assert not dismissed


# ---------------------------------------------------------------- 多语言工具行为


def test_list_symbols_java_outline(tmp_path: Path) -> None:
    """Java 文件的符号大纲走 tree-sitter，不能落到 ast.parse 上。"""
    from codesage.analyzers import java_parser
    from codesage.tools.list_symbols import make_list_symbols_tool

    if not java_parser.is_available():
        import pytest

        pytest.skip('未安装 tree-sitter-java（pip install -e ".[java]"）')

    (tmp_path / "Demo.java").write_text(
        "public class Demo {\n"
        "    public int compute(int a) throws IllegalStateException { return a; }\n"
        "}\n",
        encoding="utf-8",
    )
    tool = make_list_symbols_tool(tmp_path)
    output = tool(path="Demo.java")
    assert "符号大纲" in output
    assert "class Demo" in output
    assert "compute(int a)" in output
    assert "语法错误" not in output  # 旧实现的误导性文案绝不能出现


def test_run_analyzer_rejects_wrong_language(tmp_path: Path) -> None:
    """把 .java 喂给 ruff 必须被拦下并给出可用分析器指引。"""
    from codesage.analyzers import BanditAnalyzer, JavaRuleEngine, RuffAnalyzer
    from codesage.tools.run_analyzer import make_run_analyzer_tool

    (tmp_path / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
    tool = make_run_analyzer_tool(
        [RuffAnalyzer(), BanditAnalyzer(), JavaRuleEngine()], tmp_path
    )
    output = tool(analyzer="ruff", path="Demo.java")
    assert output.startswith("ERROR")
    assert "java_rule" in output      # 告诉模型该用哪个
    assert "不支持" in output


def test_run_analyzer_rejects_python_file_for_java_engine(tmp_path: Path) -> None:
    from codesage.analyzers import JavaRuleEngine, RuffAnalyzer
    from codesage.tools.run_analyzer import make_run_analyzer_tool

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    tool = make_run_analyzer_tool([RuffAnalyzer(), JavaRuleEngine()], tmp_path)
    output = tool(analyzer="java_rule", path="a.py")
    assert output.startswith("ERROR")
    assert "ruff" in output
