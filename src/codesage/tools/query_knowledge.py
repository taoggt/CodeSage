"""query_knowledge 工具：检索最佳实践知识库。"""

from __future__ import annotations

from typing import Any, Callable

from codesage.agent.registry import tool


def make_query_knowledge_tool(knowledge_base: Any) -> Callable[..., str]:
    @tool(name="query_knowledge")
    def query_knowledge(query: str, top_k: int = 3) -> str:
        """检索最佳实践知识库（PEP / 惯用法 / 安全 / 性能 / 设计模式卡片）。

        当需要给出“最佳实践”类建议时，先检索知识库获得权威依据，
        避免凭印象给出不准确的建议。

        Args:
            query: 检索关键词（如 "EAFP 异常处理"、"SQL 注入"、"循环性能"）
            top_k: 返回卡片数上限（默认 3）
        """
        try:
            cards = knowledge_base.query(str(query), top_k=max(1, min(int(top_k), 5)))
        except Exception as exc:  # noqa: BLE001 - 检索失败不致命
            return f"ERROR: 知识库检索失败：{exc}"

        if not cards:
            return "（知识库中没有匹配的卡片）"

        parts: list[str] = []
        for card in cards:
            tags = ", ".join(card.tags) if card.tags else "-"
            parts.append(f"### {card.title}（tags: {tags}）\n{card.body.strip()}")
        return "\n\n".join(parts)

    return query_knowledge
