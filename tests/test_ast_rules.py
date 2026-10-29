"""AST 规则引擎测试：每条规则 正例 + 反例 成对出现。

为什么正反例成对：规则的"漏报"与"误报"是两个独立缺陷，
只用正例测不出误报（over-triggering）。
"""

from codesage.analyzers.ast_rules import RuleEngine


def run_rules(source: str, filename: str = "t.py"):
    return RuleEngine().run(filename, source)


def ids(findings) -> set[str]:
    return {f.rule_id for f in findings}


# ---------------------------------------------------------------- CG001 可变默认参数


def test_cg001_mutable_default_positive():
    src = "def f(x=[]):\n    return x\n"
    assert "CG001" in ids(run_rules(src))


def test_cg001_mutable_default_negative():
    src = "def f(x=None):\n    x = x or []\n    return x\n"
    assert "CG001" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG002 裸 except


def test_cg002_bare_except_positive():
    src = "try:\n    pass\nexcept:\n    pass\n"
    assert "CG002" in ids(run_rules(src))


def test_cg002_bare_except_negative():
    src = "try:\n    pass\nexcept ValueError:\n    pass\n"
    assert "CG002" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG003 异常吞没


def test_cg003_except_pass_positive():
    src = "try:\n    a = 1\nexcept ValueError:\n    pass\n"
    assert "CG003" in ids(run_rules(src))


def test_cg003_except_pass_negative():
    src = "try:\n    a = 1\nexcept ValueError:\n    logging.warning('x')\n"
    assert "CG003" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG004 == None


def test_cg004_eq_none_positive():
    src = "x = 1\nif x == None:\n    pass\n"
    assert "CG004" in ids(run_rules(src))


def test_cg004_eq_none_negative():
    src = "x = 1\nif x is None:\n    pass\n"
    assert "CG004" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG005 open 未用 with


def test_cg005_open_without_with_positive():
    src = "f = open('a.txt')\ndata = f.read()\nf.close()\n"
    assert "CG005" in ids(run_rules(src))


def test_cg005_open_without_with_negative():
    src = "with open('a.txt') as f:\n    data = f.read()\n"
    assert "CG005" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG006 缺 docstring


def test_cg006_missing_docstring_positive():
    src = "def public_api():\n    return 1\n"
    assert "CG006" in ids(run_rules(src))


def test_cg006_missing_docstring_negative():
    src = 'def public_api():\n    """有文档。"""\n    return 1\n'
    assert "CG006" not in ids(run_rules(src))


def test_cg006_dunder_exempt():
    src = 'class A:\n    """类文档。"""\n    def __init__(self):\n        self.x = 1\n'
    assert "CG006" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG007 循环内拼接


def test_cg007_str_concat_in_loop_positive():
    src = 'text = ""\nfor x in items:\n    text += "a"\n'
    assert "CG007" in ids(run_rules(src))


def test_cg007_str_concat_in_loop_negative():
    src = "total = 0\nfor x in items:\n    total += 1\n"
    assert "CG007" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG008 列表成员检查


def test_cg008_list_membership_positive():
    src = 'if x in ["a", "b", "c"]:\n    pass\n'
    assert "CG008" in ids(run_rules(src))


def test_cg008_list_membership_negative():
    src = 'if x in {"a", "b", "c"}:\n    pass\n'
    assert "CG008" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG009 range(len())


def test_cg009_range_len_positive():
    src = "for i in range(len(items)):\n    print(items[i])\n"
    assert "CG009" in ids(run_rules(src))


def test_cg009_range_len_negative():
    src = "for i, item in enumerate(items):\n    print(i, item)\n"
    assert "CG009" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG010 过宽 except


def test_cg010_broad_except_positive():
    src = "try:\n    a = 1\nexcept Exception:\n    raise\n"
    assert "CG010" in ids(run_rules(src))


def test_cg010_broad_except_negative():
    src = "try:\n    a = 1\nexcept OSError:\n    raise\n"
    assert "CG010" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG011 eval/exec


def test_cg011_eval_positive():
    src = "result = eval(user_input)\n"
    assert "CG011" in ids(run_rules(src))


def test_cg011_eval_negative():
    src = "import ast\nresult = ast.literal_eval(user_input)\n"
    assert "CG011" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG012 硬编码密钥


def test_cg012_hardcoded_secret_positive():
    src = 'api_key = "sk-live-9f8e7d6c5b4a"\n'
    assert "CG012" in ids(run_rules(src))


def test_cg012_hardcoded_secret_negative_placeholder():
    src = 'password = "changeme"\n'
    assert "CG012" not in ids(run_rules(src))


# ---------------------------------------------------------------- CG013 命令注入


def test_cg013_shell_true_positive():
    src = 'import subprocess\nsubprocess.run(cmd, shell=True)\n'
    assert "CG013" in ids(run_rules(src))


def test_cg013_shell_true_negative():
    src = 'import subprocess\nsubprocess.run(["ls", "-la"])\n'
    assert "CG013" not in ids(run_rules(src))


def test_cg013_os_system_positive():
    src = 'import os\nos.system("rm -rf /")\n'
    assert "CG013" in ids(run_rules(src))


# ---------------------------------------------------------------- 引擎行为


def test_syntax_error_reported():
    findings = run_rules("def broken(:\n    pass\n")
    assert len(findings) == 1
    assert "语法错误" in findings[0].title


def test_rule_exception_does_not_crash():
    """单条规则抛异常不影响其他规则（引擎容错）。"""

    class ExplodingRule:
        id = "BOOM"

        def check(self, tree, ctx):
            raise RuntimeError("boom")

    from codesage.analyzers.ast_rules import MutableDefaultArgRule

    engine = RuleEngine(rules=[ExplodingRule(), MutableDefaultArgRule()])
    findings = engine.run("t.py", "def f(x=[]):\n    return x\n")
    assert "CG001" in ids(findings)
