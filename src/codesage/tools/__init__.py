"""工具集：Agent 可调用的能力。

设计模式：工厂函数 + 依赖注入。
每个工具通过 make_xxx_tool(...) 创建，把工作区路径、分析器、知识库、
发现收集器等依赖闭包进去，工具函数本身保持"纯函数式"接口
（只接收模型给的参数），便于测试与复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codesage.agent.registry import ToolRegistry
from codesage.core.models import Finding
from codesage.tools.dismiss_candidate import make_dismiss_candidate_tool
from codesage.tools.list_symbols import make_list_symbols_tool
from codesage.tools.query_knowledge import make_query_knowledge_tool
from codesage.tools.read_file import make_read_file_tool
from codesage.tools.run_analyzer import make_run_analyzer_tool
from codesage.tools.search_code import make_search_code_tool
from codesage.tools.submit_finding import make_submit_finding_tool

__all__ = [
    "build_default_tools",
    "make_dismiss_candidate_tool",
    "make_list_symbols_tool",
    "make_query_knowledge_tool",
    "make_read_file_tool",
    "make_run_analyzer_tool",
    "make_search_code_tool",
    "make_submit_finding_tool",
]


def build_default_tools(
    *,
    workspace: Path,
    analyzers: list[Any],
    knowledge_base: Any,
    findings_collector: list[Finding],
    dismissed_collector: list[dict] | None = None,
    read_limit: int = 500,
    visit_log: list[str] | None = None,
    candidates: list[Finding] | None = None,
) -> ToolRegistry:
    """组装默认工具集。

    参数：
    - workspace: 工作区根目录（所有工具被限制在此目录内）
    - analyzers: 可调用的确定性分析器列表
    - knowledge_base: 知识库实例（可为 None，则不注册该工具）
    - findings_collector: LLM 发现的收集列表（submit_finding 写入目标）
    - dismissed_collector: 被驳回候选的记录列表（dismiss_candidate 写入目标）
    - visit_log: 已读文件台账（read_file 写入，压缩/覆盖率统计读取）
    - candidates: 确定性候选发现清单（dismiss_candidate 校验驳回目标是否存在）
    """
    registry = ToolRegistry()
    registry.register(make_read_file_tool(workspace, limit=read_limit, visit_log=visit_log))
    registry.register(make_search_code_tool(workspace))
    registry.register(make_list_symbols_tool(workspace))
    if analyzers:
        registry.register(make_run_analyzer_tool(analyzers, workspace))
    if knowledge_base is not None:
        registry.register(make_query_knowledge_tool(knowledge_base))
    registry.register(make_submit_finding_tool(findings_collector, workspace))
    if dismissed_collector is not None:
        registry.register(
            make_dismiss_candidate_tool(
                dismissed_collector,
                workspace=workspace,
                candidates=candidates,
            )
        )
    return registry
