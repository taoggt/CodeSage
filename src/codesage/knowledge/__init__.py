"""知识库：替换为向量检索时本包的对外接口保持不变。"""

from codesage.knowledge.loader import KnowledgeBase, KnowledgeCard, tokenize

__all__ = ["KnowledgeBase", "KnowledgeCard", "tokenize"]


def default_knowledge_base() -> KnowledgeBase:
    """加载包内自带的卡片语料。"""
    return KnowledgeBase()
