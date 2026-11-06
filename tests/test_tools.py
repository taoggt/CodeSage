"""工具层测试：submit_finding 的文本归一化与去重行为。

背景：真实使用中发现部分模型通过 function calling 提交多行文本时
会双重转义——JSON 解析后剩下字面 "\n" 序列（显示为一串 \n），
本文件锁定归一化修复的行为（含防止误伤代码字面量的保守策略）。
"""

from pathlib import Path

from codesage.tools.submit_finding import make_submit_finding_tool


def make_tool():
    collector = []
    tool = make_submit_finding_tool(collector, Path("."))
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


def test_double_escaped_newlines_normalized():
    """模型双重转义（字面反斜杠+n）应被还原为真实换行。"""
    collector, tool = make_tool()
    submit(
        tool,
        suggestion="改用参数列表形式：\\n\\n    import subprocess\\n    run()",
    )
    suggestion = collector[0].suggestion
    assert "\n" in suggestion          # 已还原为真实换行
    assert "\\n" not in suggestion     # 不再有字面序列
    assert "    import subprocess" in suggestion


def test_real_newlines_untouched():
    collector, tool = make_tool()
    submit(tool, suggestion="正常第一行\n正常第二行")
    assert collector[0].suggestion == "正常第一行\n正常第二行"


def test_single_escape_literal_kept():
    """单个字面 \\n 通常是代码里的字符串字面量，不应被误还原。"""
    collector, tool = make_tool()
    submit(tool, suggestion='print("a\\nb") 的正确用法')
    assert "\\n" in collector[0].suggestion   # 保留字面
    assert "\n" not in collector[0].suggestion


def test_description_also_normalized():
    collector, tool = make_tool()
    submit(tool, description="触发条件如下：\\n第一段\\n第二段")
    assert "\n" in collector[0].description


def test_dedup_same_location_keeps_higher_confidence():
    collector, tool = make_tool()
    submit(tool, confidence=0.6, title="低置信")
    submit(tool, confidence=0.95, title="高置信")
    assert len(collector) == 1
    assert collector[0].title == "高置信"


def test_invalid_enum_returns_error_text():
    collector, tool = make_tool()
    output = submit(tool, category="not_a_category")
    assert output.startswith("ERROR")
    assert not collector


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
