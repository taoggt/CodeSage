"""语言注册表测试：单一真相源的完整性。

为什么值得单独一个文件：注册表是 v2 多语言的地基——准入判断、测试代码豁免、
Prompt 人设、导出围栏、前端高亮全都读它。这里锁住三件事：
1. 每个已注册语言都有必备字段（缺字段会在很远的地方炸）；
2. 后缀 → 语言的映射无歧义；
3. 语言模式解析的边界（非法值必须报错，不能静默降级）。
"""

from __future__ import annotations

import pytest

from codesage.core.languages import (
    AUTO_MODE,
    LANGUAGE_SPECS,
    Language,
    LanguageSpec,
    all_extensions,
    all_languages,
    all_specs,
    describe_languages,
    extensions_label,
    is_test_path,
    language_for_path,
    language_spec,
    language_spec_for_path,
    parse_language_mode,
)


def test_every_language_has_required_fields():
    for spec in all_specs():
        assert isinstance(spec, LanguageSpec)
        assert spec.language in LANGUAGE_SPECS
        assert spec.display, f"{spec.code} 缺少展示名"
        assert spec.extensions, f"{spec.code} 缺少扩展名"
        assert all(ext.startswith(".") for ext in spec.extensions)
        assert spec.markdown_fence, f"{spec.code} 缺 markdown 围栏"
        assert spec.system_prompt_role, f"{spec.code} 缺 Prompt 角色名"
        assert spec.code == spec.language.value


def test_extensions_are_unambiguous():
    """一个后缀只能属于一种语言——否则准入判断的结果取决于字典顺序。"""
    seen: dict[str, str] = {}
    for spec in all_specs():
        for ext in spec.extensions:
            assert ext not in seen, f"{ext} 同时属于 {seen[ext]} 与 {spec.code}"
            seen[ext] = spec.code


def test_language_detection_by_suffix():
    assert language_for_path("a/b/service.py") is Language.PYTHON
    assert language_for_path("src/main/java/Foo.java") is Language.JAVA
    assert language_for_path("Foo.JAVA") is Language.JAVA  # 大小写不敏感
    assert language_for_path("web/app.ts") is None
    assert language_for_path("README.md") is None


def test_spec_lookup_returns_none_for_unknown():
    assert language_spec_for_path("x.kt") is None
    with pytest.raises(ValueError):
        language_spec("kotlin")


def test_all_extensions_cover_registry():
    assert set(all_extensions()) == {".py", ".java"}


def test_parse_auto_mode_covers_all_languages():
    mode, languages = parse_language_mode("auto")
    assert mode == AUTO_MODE
    assert languages == all_languages()

    # 缺省与空串都视为 auto（配置未填写时不该报错）
    assert parse_language_mode(None)[0] == AUTO_MODE
    assert parse_language_mode("")[0] == AUTO_MODE


def test_parse_single_and_multi_language():
    assert parse_language_mode("python") == ("python", (Language.PYTHON,))
    assert parse_language_mode("java") == ("java", (Language.JAVA,))
    assert parse_language_mode("python,java") == (
        "python,java",
        (Language.PYTHON, Language.JAVA),
    )
    # 顺序无关、去重、容忍空格
    assert parse_language_mode(" Java , python ,java ") == (
        "java,python",
        (Language.JAVA, Language.PYTHON),
    )


def test_parse_invalid_language_raises():
    """非法值必须显式报错：配置项写错却静默按 auto 跑，是最难排查的故障。"""
    with pytest.raises(ValueError) as exc:
        parse_language_mode("kotlin")
    assert "kotlin" in str(exc.value)


def test_describe_helpers():
    assert describe_languages((Language.PYTHON, Language.JAVA)) == "Python / Java"
    assert extensions_label((Language.PYTHON,)) == ".py"
    assert extensions_label((Language.PYTHON, Language.JAVA)) == ".py / .java"


def test_python_test_file_conventions():
    spec = language_spec(Language.PYTHON)
    for path in (
        "tests/test_a.py",
        "test_a.py",
        "a_test.py",
        "pkg/tests/helper.py",
        "conftest.py",
        "tests\\test_a.py",  # Windows 反斜杠
    ):
        assert is_test_path(spec, path), path
    for path in ("src/app.py", "src/contest.py", "latest_main.py"):
        assert not is_test_path(spec, path), path


def test_java_test_file_conventions():
    """Java 与 pytest 约定完全不同：靠 src/test/java 与 *Test/*IT 命名。"""
    spec = language_spec(Language.JAVA)
    for path in (
        "src/main/java/com/x/UserServiceTest.java",
        "src/main/java/com/x/UserServiceTests.java",
        "src/main/java/com/x/UserServiceIT.java",
        "src/test/java/com/x/UserService.java",
    ):
        assert is_test_path(spec, path), path
    for path in (
        "src/main/java/com/x/UserService.java",
        "src/main/java/com/x/Tester.java",  # 结尾不是 Test
        "src/main/java/com/x/Contest.java",
    ):
        assert not is_test_path(spec, path), path


def test_java_spec_excludes_build_outputs():
    """Java 仓库必须剪掉 target/.gradle/out，否则会扫上千生成文件。"""
    spec = language_spec(Language.JAVA)
    assert "target" in spec.extra_excludes
    assert ".gradle" in spec.extra_excludes
    assert "out" in spec.extra_excludes
    # Java 类文件偏长：阈值应高于 Python 的全局默认
    assert spec.max_file_lines is not None and spec.max_file_lines > 2000


def test_spec_is_immutable():
    """frozen 防误改：全局共享的 spec 被就地改写会造成跨请求的诡异状态。"""
    spec = language_spec(Language.JAVA)
    with pytest.raises(Exception):
        spec.display = "Kotlin"  # type: ignore[misc]
