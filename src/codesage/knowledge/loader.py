"""知识库：Markdown 卡片 + 轻量关键词检索。

为什么不用向量检索（v1 取舍）：
- 卡片总量可控（几十张），关键词/标签匹配的检索质量与向量检索差距很小；
- 省下一周的 embedding 选型 + 向量库集成时间（对齐一个月工期的现实约束）；
- 演进路径清晰：query() 接口不变，v2 换成 hybrid retriever 即可。

卡片格式（YAML frontmatter 的简化子集）：
    ---
    id: py-eafp
    title: EAFP 优先于 LBYL
    tags: [异常处理, 惯用法]
    triggers: [try, except, KeyError, in dict]
    ---
    正文（Markdown）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ENG_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


@dataclass
class KnowledgeCard:
    """一张知识卡片。"""

    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    path: str = ""


def _parse_list_value(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    items = [item.strip().strip("'\"") for item in raw.split(",")]
    return [item for item in items if item]


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """解析简化 frontmatter，返回 (元数据, 正文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, object] = {}
    end_idx = -1
    for idx in range(1, len(lines)):
        line = lines[idx].strip()
        if line == "---":
            end_idx = idx
            break
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("["):
                meta[key] = _parse_list_value(value)
            else:
                meta[key] = value
    if end_idx < 0:
        return {}, text
    body = "\n".join(lines[end_idx + 1:]).strip()
    return meta, body


def tokenize(text: str) -> set[str]:
    """轻量分词：英文/标识符单词 + 中文 2-gram。

    中文没有空格分词，2-gram 是最简单有效的近似
    （"异常处理" → {"异常", "常处", "处理"}）。
    """
    tokens: set[str] = set()
    for word in _ENG_RE.findall(text.lower()):
        tokens.add(word)
    for seg in _CJK_RE.findall(text):
        if len(seg) == 1:
            tokens.add(seg)
        for i in range(len(seg) - 1):
            tokens.add(seg[i : i + 2])
    return tokens


class KnowledgeBase:
    """卡片知识库。"""

    def __init__(self, corpus_dir: Path | None = None) -> None:
        self._dir = Path(corpus_dir) if corpus_dir else Path(__file__).parent / "corpus"
        self._cards: list[KnowledgeCard] = []
        self._load()

    @property
    def cards(self) -> list[KnowledgeCard]:
        return list(self._cards)

    def _load(self) -> None:
        if not self._dir.is_dir():
            return
        for path in sorted(self._dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            cards_meta = meta.get("cards")
            # 一个文件可以包含多张卡片（用 <!-- card --> 分隔）？
            # v1 简化：一个文件 = 一张卡片
            self._cards.append(
                KnowledgeCard(
                    id=str(meta.get("id", path.stem)),
                    title=str(meta.get("title", path.stem)),
                    tags=list(meta.get("tags", []) or []),  # type: ignore[arg-type]
                    triggers=list(meta.get("triggers", []) or []),  # type: ignore[arg-type]
                    body=body,
                    path=path.name,
                )
            )
            _ = cards_meta  # 预留多卡片文件扩展位

    def query(self, text: str, top_k: int = 3) -> list[KnowledgeCard]:
        """关键词打分检索：tags 命中 ×2，triggers 命中 ×3，标题命中 ×2。"""
        query_tokens = tokenize(text)
        if not query_tokens:
            return []

        scored: list[tuple[int, KnowledgeCard]] = []
        for card in self._cards:
            tag_tokens = tokenize(" ".join(card.tags))
            trigger_tokens = tokenize(" ".join(card.triggers))
            title_tokens = tokenize(card.title)
            score = (
                len(query_tokens & tag_tokens) * 2
                + len(query_tokens & trigger_tokens) * 3
                + len(query_tokens & title_tokens) * 2
            )
            if score > 0:
                scored.append((score, card))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [card for _, card in scored[: max(1, top_k)]]
