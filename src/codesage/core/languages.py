"""语言注册表：多语言支持的单一真相源。

为什么需要这个模块
------------------
v1 的"只支持 Python"这个假设散落在四个地方：`ingest/filters.py` 的后缀判断、
`analyzers/ast_rules.py` 的 `ast.parse`、`analyzers/__init__.py` 的硬编码三元组、
以及 prompt 里的"资深 Python 代码审查专家"。任何一处漏改都会造成
"看起来支持了、实际半残"——文件进得来但分析器报假语法错误，或者
报告里全是 Python 的话术。

所以把"一种语言是什么样"集中成**数据**（LanguageSpec），代码只消费字段。
新增一门语言 = 新增一条 spec + 一个规则引擎，而不是去内核里加 if 分支。

设计原则（面试可讲）
--------------------
数据驱动优于条件分支：判断"这个文件能不能审"应该是一次后缀查表，
而不是 `if suffix == ".py"`。后缀表由注册表在 import 时构建一次，
新增语言不需要改任何判断逻辑。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

# 语言模式：默认自动探测（按扫描到的后缀决定实际审查哪些语言）
AUTO_MODE = "auto"


class Language(str, Enum):
    """已支持的语言。值与报告 JSON 里的 `language` 字段一致。"""

    PYTHON = "python"
    JAVA = "java"


@dataclass(frozen=True)
class LanguageSpec:
    """一种语言的全部静态特征。

    这里的每个字段都有唯一消费方（见下方注释）。刻意不做成"大而全的
    语言元数据"，只收录当前链路真正会读的字段——没有消费方不要加。
    """

    language: Language
    display: str                              # "Java"：报告/CLI/GUI 展示名
    extensions: tuple[str, ...]               # 准入判断（FileFilter）与预览白名单

    # ---- 扫描剪枝（pipeline 合并进 FileFilter.excludes）
    extra_excludes: tuple[str, ...] = ()      # Java: target/.gradle/out
    max_file_lines: int | None = None         # None = 用全局阈值；Java 类文件偏长

    # ---- 测试代码识别（误报控制，analyzers/base.is_test_file）
    test_file_prefixes: tuple[str, ...] = ()
    test_file_suffixes: tuple[str, ...] = ()
    test_file_globs: tuple[str, ...] = ()
    test_dirs: frozenset[str] = frozenset()
    test_path_markers: tuple[str, ...] = ()

    # ---- 文案与展示
    idiom_label: str = ""                     # "Pythonic" / "Java 惯用法"
    comment_prefix: str = "#"                 # 规则判"只有注释的 catch"用
    markdown_fence: str = "text"              # 导出代码围栏 / 前端选高亮器

    # ---- 项目画像（memory/project.py）
    framework_hints: Mapping[str, str] = field(default_factory=dict)

    # ---- Prompt
    system_prompt_role: str = ""              # "资深 Java 代码审查专家"

    # ---- 确定性层缺失时的用户可见说明（降级不静默）
    unavailable_hint: str = ""

    @property
    def code(self) -> str:
        return self.language.value

    @property
    def extensions_label(self) -> str:
        return " / ".join(self.extensions)


# Python 的测试文件规则必须与历史行为逐字等价：
# 原 analyzers/base.py 用 startswith(("test_", "conftest")) + endswith("_test.py")
# + 路径中出现 test/tests 目录。
_PYTHON_SPEC = LanguageSpec(
    language=Language.PYTHON,
    display="Python",
    extensions=(".py",),
    test_file_prefixes=("test_", "conftest"),
    test_file_suffixes=("_test.py",),
    test_dirs=frozenset({"test", "tests"}),
    idiom_label="Python 惯用法（Pythonic）",
    comment_prefix="#",
    markdown_fence="python",
    framework_hints={
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "starlette": "Starlette",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "pytest": "pytest",
        "unittest": "unittest",
        "pandas": "pandas",
        "numpy": "NumPy",
        "requests": "requests",
        "httpx": "httpx",
        "celery": "Celery",
        "redis": "Redis",
        "pymongo": "PyMongo",
        "typer": "Typer",
        "click": "Click",
        "asyncio": "asyncio",
        "aiohttp": "aiohttp",
        "torch": "PyTorch",
        "transformers": "Transformers",
    },
    system_prompt_role="资深 Python 代码审查专家",
)

# Java 的测试约定与 pytest 完全不同：JUnit 靠 *Test/*Tests/*IT 命名 + Maven/Gradle 的
# src/test/java 目录，没有 conftest 这种东西。所以单独一条 spec，不复用 Python 规则。
_JAVA_SPEC = LanguageSpec(
    language=Language.JAVA,
    display="Java",
    extensions=(".java",),
    extra_excludes=("target", ".gradle", "out", ".idea", ".settings", "bin"),
    # Java 的领域服务类常见 2000+ 行，沿用 Python 阈值会把它们静默跳过
    max_file_lines=3000,
    test_file_globs=("*Test.java", "*Tests.java", "*IT.java", "*TestCase.java"),
    test_path_markers=("src/test/java/", "src/androidTest/"),
    idiom_label="Java 惯用法（Effective Java）",
    comment_prefix="//",
    markdown_fence="java",
    framework_hints={
        "org.springframework": "Spring",
        "org.junit": "JUnit",
        "org.mockito": "Mockito",
        "org.apache.ibatis": "MyBatis",
        "com.baomidou": "MyBatis-Plus",
        "jakarta.persistence": "JPA",
        "javax.persistence": "JPA",
        "org.hibernate": "Hibernate",
        "com.fasterxml.jackson": "Jackson",
        "org.apache.kafka": "Kafka",
        "org.apache.rocketmq": "RocketMQ",
        "redis.clients": "Redis",
        "io.lettuce": "Lettuce",
        "com.alibaba.nacos": "Nacos",
        "org.slf4j": "SLF4J",
        "lombok": "Lombok",
        "com.google.common": "Guava",
        "reactor.core": "Reactor",
        "io.micrometer": "Micrometer",
        "org.apache.commons": "Commons",
    },
    system_prompt_role="资深 Java 代码审查专家",
    unavailable_hint=(
        "未安装 tree-sitter-java：Java 的确定性规则与复杂度分析已跳过"
        '（LLM 语义审查不受影响）。安装：pip install -e ".[java]"'
    ),
)

LANGUAGE_SPECS: dict[Language, LanguageSpec] = {
    Language.PYTHON: _PYTHON_SPEC,
    Language.JAVA: _JAVA_SPEC,
}

# 后缀 → 语言索引（import 时构建一次，准入判断退化为一次字典查表）
_SUFFIX_INDEX: dict[str, Language] = {
    ext.lower(): spec.language
    for spec in LANGUAGE_SPECS.values()
    for ext in spec.extensions
}


def all_specs() -> tuple[LanguageSpec, ...]:
    """全部已注册语言（顺序稳定，用于文案与 GUI 列表）。"""
    return tuple(LANGUAGE_SPECS.values())


def all_languages() -> tuple[Language, ...]:
    return tuple(LANGUAGE_SPECS)


def all_extensions() -> tuple[str, ...]:
    return tuple(_SUFFIX_INDEX)


def language_for_path(path: str | Path) -> Language | None:
    """按后缀判断语言；不认识返回 None。"""
    try:
        suffix = Path(str(path)).suffix.lower()
    except (TypeError, ValueError):
        return None
    return _SUFFIX_INDEX.get(suffix)


def language_spec_for_path(path: str | Path) -> LanguageSpec | None:
    language = language_for_path(path)
    return LANGUAGE_SPECS[language] if language is not None else None


def language_spec(language: Language | str) -> LanguageSpec:
    """取 spec；未注册时抛 ValueError（带可用值提示，便于 CLI 转退出码）。"""
    try:
        key = language if isinstance(language, Language) else Language(str(language))
    except ValueError as exc:
        raise ValueError(
            f"未支持的语言：{language}（可用：{', '.join(s.code for s in all_specs())}）"
        ) from exc
    return LANGUAGE_SPECS[key]


def describe_languages(languages: Iterable[Language]) -> str:
    """人类可读的语言列表，如 "Python / Java"。"""
    names = [LANGUAGE_SPECS[lang].display for lang in languages if lang in LANGUAGE_SPECS]
    return " / ".join(names) if names else "（无）"


def extensions_label(languages: Iterable[Language]) -> str:
    exts = [
        ext
        for lang in languages
        if lang in LANGUAGE_SPECS
        for ext in LANGUAGE_SPECS[lang].extensions
    ]
    return " / ".join(exts)


def is_test_path(spec: LanguageSpec, path: str | Path) -> bool:
    """按语言约定判断是否测试代码。

    注意 Windows 路径统一转 POSIX 分隔符后再判断——旧实现在
    `is_test_file` 里做过同样的归一化，这里保持等价。

    也要注意这里用的是 `fnmatchcase` 而不是 `fnmatch`：后者在 Windows 上会
    把两边都小写化（os.path.normcase），于是 `Contest.java` 会被 `*Test.java`
    误判为测试文件——Java 的命名约定是大小写敏感的，这属于真实误判。
    """
    normalized = str(path).replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]

    if spec.test_file_prefixes and name.startswith(spec.test_file_prefixes):
        return True
    if spec.test_file_suffixes and name.endswith(spec.test_file_suffixes):
        return True
    if spec.test_file_globs and any(
        fnmatch.fnmatchcase(name, pattern) for pattern in spec.test_file_globs
    ):
        return True

    parent_parts = normalized.split("/")[:-1]
    if spec.test_dirs and any(part in spec.test_dirs for part in parent_parts):
        return True
    if spec.test_path_markers and any(
        marker in normalized for marker in spec.test_path_markers
    ):
        return True
    return False


def parse_language_mode(raw: str | None) -> tuple[str, tuple[Language, ...]]:
    """解析语言模式，返回 (模式名, 允许纳入的语言集合)。

    支持四种形态：
        None / "" / "auto"  → 自动探测（全部已注册语言，实际审谁由扫描结果决定）
        "python"            → 只审 Python
        "java"              → 只审 Java
        "python,java"       → 两者都审（顺序无关，去重）

    为什么 auto 返回"全部语言"而不是"探测结果"：探测的准确时刻在扫描之后
    （`ScanResult.language_counts`），扫描前的过滤只需要知道"后缀白名单"。
    提前读目录做探测等于多一次 IO，且与 gitignore/排除规则容易不一致。

    非法值抛 ValueError —— CLI 转 Exit(2)，配置由 pydantic validator 拦截。
    """
    if raw is None:
        return AUTO_MODE, all_languages()

    text = str(raw).strip().lower()
    if not text or text == AUTO_MODE:
        return AUTO_MODE, all_languages()

    requested: list[Language] = []
    for token in text.replace(" ", "").split(","):
        if not token:
            continue
        try:
            language = Language(token)
        except ValueError as exc:
            raise ValueError(
                f"未知语言：{token}（可用：{AUTO_MODE} / "
                f"{' / '.join(s.code for s in all_specs())}，多选用逗号分隔）"
            ) from exc
        if language not in requested:
            requested.append(language)

    if not requested:
        return AUTO_MODE, all_languages()
    return ",".join(lang.value for lang in requested), tuple(requested)
