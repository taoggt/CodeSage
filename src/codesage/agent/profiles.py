"""角色 Profile：系统 Prompt 与审查维度。

设计说明：
- 基础守则（BASE_SYSTEM_PROMPT）是审查质量的关键资产，包含
  复核流程、证据要求、禁止事项（泛泛建议 / 臆测代码 / 重复提交）；
- Profile 只负责"聚焦维度"的差异化（full / security / performance），
  避免为多角色而多角色——实现上是同一个循环 + 不同 Prompt。

多语言改造的原则（重要）
------------------------
**Python 段落逐字保留**。Prompt 是"行为资产"：改写措辞会改变模型输出，
历史 A/B 对比随即失效，评测基线也会漂移。所以这里只把三类**语言相关**的
措辞抽成变量（人设、测试惯例、惯用法标签），骨架与其余条目一个字符不动。
Java 的差异放在 `LANGUAGE_GUIDES` 里新增，不覆写 Python 的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from codesage.core.languages import Language, all_languages, describe_languages, language_spec


@dataclass
class Profile:
    """一个审查角色。"""

    name: str
    title: str
    focus: str = ""  # 追加的聚焦说明（空 = 五维度全面审查）


@dataclass(frozen=True)
class LanguageGuide:
    """某语言在 Prompt 里的差异部分（其余全部共用）。"""

    role: str                 # 人设："资深 Java 代码审查专家"
    test_conventions: str     # 测试代码惯例说明（第 7 条正文）
    idiom_label: str          # "Java 惯用法（Effective Java）"
    extra_dimensions: str = ""  # 追加的审查维度（仅该语言相关）


LANGUAGE_GUIDES: dict[Language, LanguageGuide] = {
    Language.PYTHON: LanguageGuide(
        role="资深 Python 代码审查专家",
        test_conventions=(
            "`tests/` 目录与 `test_*.py` 文件中，\n"
            "   大量使用 assert、辅助函数不写 docstring、fixture 硬编码临时路径\n"
            "   等属于测试领域的标准做法——不要提交此类发现，只报告\n"
            "   测试代码中真正的逻辑错误（如断言写反、mock 失效）。"
        ),
        idiom_label="Python 惯用法（Pythonic）",
    ),
    Language.JAVA: LanguageGuide(
        role="资深 Java 代码审查专家",
        test_conventions=(
            "`src/test/java` 目录与 `*Test.java` / `*Tests.java` / `*IT.java` 文件中，\n"
            "   大量使用断言、测试方法天然多分支、fixture 中写死测试数据\n"
            "   等属于测试领域的标准做法——不要提交此类发现，只报告\n"
            "   测试代码中真正的逻辑错误（如断言写反、断言被吞、mock 失效）。"
        ),
        idiom_label="Java 惯用法（Effective Java）",
        extra_dimensions=(
            "- 并发与共享状态：线程安全、静态可变字段、连接/会话复用、锁粒度与死锁风险\n"
            "- 框架陷阱：事务边界（`@Transactional` 自调用失效）、N+1 查询、"
            "实体与 DTO 混用、异常映射"
        ),
    ),
}

_DEFAULT_GUIDE = LANGUAGE_GUIDES[Language.PYTHON]


BASE_SYSTEM_PROMPT = """你是 CodeSage，一名{role}（Agent）。

工作方式：通过工具读取真实代码，基于证据给出结论。
每条问题必须通过 submit_finding 工具以结构化方式提交；
不要在对话正文中用自然语言罗列问题（对话正文只用于简短汇报进度）。

## 审查守则（必须遵守）

1. **先复核候选**：初始上下文给出了确定性分析的候选发现，逐一读取相关代码核实：
   - **属实** → 用 submit_finding 提交你的版本（含结合代码上下文的修复建议）。
     去重时你的版本优先于工具原始版本——提交后报告中展示的将是你的分析；
     不提交则报告只保留工具的通用提示（如"请查阅文档"，缺乏具体修复方案）。
   - **误报** → 用 dismiss_candidate 驳回（会从最终报告中移除），
     不要让已核实为误报的候选继续留在报告里。
2. **补充独立发现**：重点找规则抓不到的问题——逻辑错误、
   边界条件（空值/None/越界/空集合）、资源泄漏、异常路径、
   并发与共享状态污染。
3. **证据要求**：每条发现必须引用具体文件与行号，行号以 read_file
   返回的编号为准，禁止臆测未读过的代码。
4. **宁可漏报，不可误报**：证据不足时不提交——误报会直接降低
   工具的可信度。不确定的判断可以通过 reading 更多上下文来确认。
5. **建议必须可执行**：给出具体改法（改后代码或明确的操作），
   禁止"建议优化性能"这类空话。
6. **效率**：大文件分段读取（read_file 指定行号范围）；
   定位符号用 search_code / list_symbols，不要通读整库。
7. **尊重测试代码惯例**：{test_conventions}

## 审查维度清单（提交前自检覆盖情况）

- 代码质量：命名、风格一致性、注释与 docstring 完整性
- 潜在 Bug：逻辑错误、边界条件、异常处理、资源管理
- 性能：算法复杂度、循环内重复计算、数据结构选择
- 安全：注入（SQL / 命令）、反序列化、硬编码密钥、路径处理
- 最佳实践：{idiom_label}、设计模式适用性（切忌过度设计）{extra_dimensions}
"""


PROFILES: dict[str, Profile] = {
    "full": Profile(
        name="full",
        title="全面审查（五维度）",
        focus="",
    ),
    "security": Profile(
        name="security",
        title="安全聚焦",
        focus=(
            "本次审查聚焦安全维度：注入类（SQL / 命令 / 模板）、"
            "反序列化与 eval、硬编码凭据、路径穿越、SSRF、"
            "不安全随机数。其它维度仅在有严重问题时顺带提及。"
        ),
    ),
    "performance": Profile(
        name="performance",
        title="性能聚焦",
        focus=(
            "本次审查聚焦性能维度：算法复杂度（O(n²) 及以上）、"
            "循环内重复计算 / 重复属性查找、错误的数据结构选择、"
            "不必要的物化与拷贝、I/O 与网络调用模式。"
            "其它维度仅在有严重问题时顺带提及。"
        ),
    ),
}


def _resolve_guide(languages: Iterable[Language] | None) -> LanguageGuide:
    """按本次审查的语言集合挑选/合并 Prompt 差异段。

    - 未指定（None / 空）→ Python：保证 v1 的 Prompt **逐字不变**；
    - 单语言 → 该语言的 guide；
    - 混合仓库 → 人设写成并列，测试惯例与惯用法按语言逐条列出。
    """
    selected: list[Language] = []
    for language in languages or ():
        if language not in selected and language in LANGUAGE_GUIDES:
            selected.append(language)

    if not selected:
        return _DEFAULT_GUIDE
    if len(selected) == 1:
        return LANGUAGE_GUIDES[selected[0]]

    guides = [LANGUAGE_GUIDES[language] for language in selected]
    displays = describe_languages(selected)
    return LanguageGuide(
        role=f"资深代码审查专家（{displays}）",
        test_conventions="\n   ".join(
            f"（{language_spec(language).display}）{LANGUAGE_GUIDES[language].test_conventions}"
            for language in selected
        ),
        idiom_label="、".join(guide.idiom_label for guide in guides),
        extra_dimensions="\n".join(
            guide.extra_dimensions for guide in guides if guide.extra_dimensions
        ),
    )


def build_system_prompt(
    profile_name: str = "full",
    *,
    workspace_summary: str = "",
    lessons_block: str = "",
    budget_hint: str = "",
    languages: Iterable[Language] | None = None,
) -> str:
    """组装系统提示词。

    参数：
    - workspace_summary: 项目背景（来自项目级记忆）
    - lessons_block: 错题本经验（自优化的注入点）
    - budget_hint: 资源提示（如"剩余可用轮次"）
    - languages: 本次审查涉及的语言（None = Python，保持 v1 行为不变）
    """
    guide = _resolve_guide(languages)
    extra = f"\n{guide.extra_dimensions}" if guide.extra_dimensions else ""
    parts: list[str] = [
        BASE_SYSTEM_PROMPT.format(
            role=guide.role,
            test_conventions=guide.test_conventions,
            idiom_label=guide.idiom_label,
            extra_dimensions=extra,
        )
    ]

    profile = PROFILES.get(profile_name)
    if profile and profile.focus:
        parts.append(f"## 本次聚焦\n{profile.focus}")

    if workspace_summary:
        parts.append(f"## 项目背景\n{workspace_summary}")

    if lessons_block:
        parts.append(
            "## 历史经验（来自反馈错题本，供参考）\n"
            "以下模式曾被人工判定为误报或漏报，请在判断时注意：\n"
            f"{lessons_block}"
        )

    if budget_hint:
        parts.append(f"## 资源说明\n{budget_hint}")

    return "\n\n".join(parts)


__all__ = [
    "BASE_SYSTEM_PROMPT",
    "LANGUAGE_GUIDES",
    "LanguageGuide",
    "PROFILES",
    "Profile",
    "all_languages",
    "build_system_prompt",
]
