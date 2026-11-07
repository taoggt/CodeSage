"""2026-11-07 循环 / 压缩 / 去重审计的回归测试。

针对六条被确认的问题逐条上锁，避免日后改回去：
1. LLM 失败重试一次，仍失败则降级输出（不再一次抛错就整段收工）；
   连续工具错误会注入纠偏提示，不让错误事实继续累积。
2. 任务简报与 StopPolicy 共用同一个 effective_turns。
3. 压缩钉住块由确定性状态重建，不交给 LLM；tool_calls 参数进压缩输入；
   过胖摘要视为失败。
4. 判重不看 category；同位置不同标题并报；去掉 line//3 分桶边界。
5. 预算可达性 / 覆盖率写入报告 metadata。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codesage.agent.loop import AgentLoop, LoopConfig
from codesage.agent.policies import StopPolicy
from codesage.agent.registry import ToolRegistry, tool
from codesage.core.config import BudgetSettings, Settings
from codesage.core.errors import LLMError
from codesage.core.models import (
    Category,
    Finding,
    Severity,
    Source,
    dedup_findings,
    same_issue,
)
from codesage.llm.budget import TokenBudget
from codesage.llm.client import LLMResponse, MockLLMClient, ToolCall
from codesage.llm.tokenizer import HeuristicEstimator
from codesage.memory.working import WorkingMemory
from codesage.pipeline import ReviewOptions, run_review


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool(name="echo")
    def echo(text: str) -> str:
        """回显文本。

        Args:
            text: 要回显的文本
        """
        return f"echo:{text}"

    registry.register(echo)
    return registry


def _loop(client: Any, **kwargs: Any) -> tuple[AgentLoop, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    loop = AgentLoop(
        client=client,
        registry=_registry(),
        memory=WorkingMemory(active_window_turns=4),
        budget=TokenBudget(BudgetSettings()),
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=10),
        config=LoopConfig(),
        on_event=lambda e, p: events.append((e, p)),
        **kwargs,
    )
    return loop, events


class _FlakyClient:
    """前 N 次调用抛 LLMError，之后返回正常响应。"""

    def __init__(self, failures: int, response: LLMResponse) -> None:
        self.left = failures
        self.response = response
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None, model=None, temperature=None) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if self.left > 0:
            self.left -= 1
            raise LLMError("模拟上游超时")
        return self.response


# ---------------------------------------------------------------- 1. 循环鲁棒性


def test_llm_error_retries_once_and_does_not_consume_a_turn():
    client = _FlakyClient(1, LLMResponse(content="完成"))
    loop, events = _loop(client)
    result = loop.run([{"role": "user", "content": "开始"}])
    assert result.status == "finished"
    assert result.llm_failures == 1
    assert result.turns == 0            # 重试不消耗轮次
    assert len(client.calls) == 2
    assert "llm_error" in [e for e, _ in events]


def test_llm_error_after_retry_degrades_instead_of_hard_error():
    client = _FlakyClient(5, LLMResponse(content="完成"))
    loop, events = _loop(client)
    result = loop.run([{"role": "user", "content": "开始"}])
    assert result.status == "degraded"
    assert result.llm_failures == 2
    assert "降级完成" in result.stop_reason
    assert "degraded" in [e for e, _ in events]


def test_consecutive_tool_errors_inject_correction_message():
    """连续两轮工具失败后，模型必须收到「别基于失败结果推理」的提示。"""
    bad = ToolCall(id="t1", name="not_exist", arguments={}, raw_arguments="{}")
    responses = [LLMResponse(content="", tool_calls=[bad]) for _ in range(3)]
    responses.append(LLMResponse(content="完成"))
    client = MockLLMClient(responses)
    loop, events = _loop(client)
    loop.run([{"role": "user", "content": "开始"}])
    third_request = client.calls[2]
    assert any(
        m.get("role") == "system" and "不要基于失败结果" in str(m.get("content", ""))
        for m in third_request
    )
    assert "tool_error_streak" in [e for e, _ in events]


# ---------------------------------------------------------------- 2. 轮次单一真相源


def _pipeline_settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.llm.api_key = "test-key"          # 只为让 llm_configured 为 True
    settings.review.store_path = str(tmp_path / "store.db")
    return settings


def _install_mock_client(monkeypatch, client: MockLLMClient) -> None:
    monkeypatch.setattr("codesage.pipeline.LLMClient", lambda _settings: client)


def test_brief_and_policy_use_same_effective_turns(tmp_path: Path, monkeypatch):
    """--max-turns 3 时，简报里必须是 3，且循环真的在第 3 轮停。"""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    call = ToolCall(
        id="t1",
        name="read_file",
        arguments={"path": "a.py"},
        raw_arguments=json.dumps({"path": "a.py"}),
    )
    client = MockLLMClient([LLMResponse(content="", tool_calls=[call]) for _ in range(8)])
    _install_mock_client(monkeypatch, client)

    report = run_review(
        str(tmp_path),
        _pipeline_settings(tmp_path),
        ReviewOptions(max_turns=3, persist=False),
    )
    system_prompt = client.calls[0][0]["content"]
    assert "最多可执行 3 个回合" in system_prompt
    assert "最多可执行 12" not in system_prompt
    assert report.turns == 3
    assert report.metadata["max_turns"] == 3
    assert report.metadata["budget_diagnostics"]["max_turns"] == 3


def test_default_turns_still_reported_as_configured(tmp_path: Path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    client = MockLLMClient([LLMResponse(content="完成")])
    _install_mock_client(monkeypatch, client)
    report = run_review(
        str(tmp_path), _pipeline_settings(tmp_path), ReviewOptions(persist=False)
    )
    assert "最多可执行 12 个回合" in client.calls[0][0]["content"]
    assert report.metadata["max_turns"] == 12


def test_recommended_long_session_config_really_triggers_compression():
    """Q1 的另一半：调大轮次 + 调小预算后，压缩链路确实可达。

    默认 12 轮 / 120k 预算下压缩永不触发（见上一条诊断测试）；这里用
    codesage.toml.example 里的推荐组合证明机制本身没坏，只是参数不匹配。
    """
    big = "x" * 6000
    call = ToolCall(
        id="t1",
        name="echo",
        arguments={"text": big},
        raw_arguments=json.dumps({"text": big}),
    )
    responses = [LLMResponse(content="", tool_calls=[call]) for _ in range(24)]
    client = MockLLMClient(responses)
    loop = AgentLoop(
        client=client,
        registry=_registry(),
        memory=WorkingMemory(active_window_turns=4),
        budget=TokenBudget(BudgetSettings(context_window=60000, budget_window=60000)),
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=24),
        config=LoopConfig(),
        compressor=lambda _p: "推理摘要",
    )
    result = loop.run([{"role": "system", "content": "系统提示"}])
    assert result.compress_events > 0
    assert result.turns == 24


def test_budget_diagnostics_expose_unreachable_compression(tmp_path: Path, monkeypatch):
    """默认配置下压缩不触发，必须有一条可观测数据说明原因。"""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    client = MockLLMClient([LLMResponse(content="完成")])
    _install_mock_client(monkeypatch, client)
    report = run_review(
        str(tmp_path), _pipeline_settings(tmp_path), ReviewOptions(persist=False)
    )
    diag = report.metadata["budget_diagnostics"]
    assert diag["compression_triggered"] is False
    assert diag["soft_threshold_tokens"] == 72000
    assert diag["peak_context_tokens"] < 72000
    assert "压缩未触发" in diag["note"]
    assert report.metadata["coverage_ratio"] == 0.0
    assert report.metadata["llm_failures"] == 0
    assert report.metadata["llm_degraded"] is False


# ---------------------------------------------------------------- 3. 压缩保真


def _fill(mem: WorkingMemory, turns: int) -> None:
    for i in range(turns):
        mem.append({"role": "assistant", "content": f"a{i}"})
        mem.append({"role": "tool", "content": f"t{i}"})


def test_pinned_block_is_rebuilt_not_summarized():
    mem = WorkingMemory(active_window_turns=1)
    _fill(mem, 3)
    prompts: list[str] = []

    def compressor(prompt: str) -> str:
        prompts.append(prompt)
        return "推理摘要"

    assert mem.compress(compressor, pinned="已提交发现 #1：a.py:4 命令注入") is not None
    assert mem.pinned == "已提交发现 #1：a.py:4 命令注入"
    assert any(
        "a.py:4 命令注入" in str(m.get("content", "")) for m in mem.build()
    )
    # 权威事实不该交给 LLM：钉住块不出现在压缩输入里
    assert all("a.py:4 命令注入" not in p for p in prompts)

    _fill(mem, 2)
    mem.compress(compressor, pinned="已提交发现 #1、#2")
    text = "\n".join(str(m.get("content", "")) for m in mem.build())
    assert "已提交发现 #1、#2" in text
    assert text.count("系统记录（确定性重建") == 1   # 不重复累积


def test_bloated_summary_rejected_and_state_kept():
    """摘要比原文还长只会让上下文变胖，必须判为压缩失败。"""
    mem = WorkingMemory(active_window_turns=1)
    _fill(mem, 3)
    assert mem.compress(lambda _p: "x" * 300) is None
    assert mem.last_compress_reason == "bloated_summary"
    assert not mem.compressed
    assert mem.active_count == 6


def test_compress_attempt_reasons_are_observable():
    mem = WorkingMemory(active_window_turns=2)
    _fill(mem, 2)
    assert mem.compress(lambda _p: "摘要") is None
    assert mem.last_compress_reason == "no_need"


def test_tool_call_arguments_enter_compression_payload():
    """旧版 _serialize 只写工具名，finding 正文根本没进压缩输入。"""
    mem = WorkingMemory(active_window_turns=1)
    args = json.dumps(
        {"title": "命令注入", "description": "user 可控"}, ensure_ascii=False
    )
    mem.append(
        {
            "role": "assistant",
            "content": "提交发现",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "submit_finding", "arguments": args},
                }
            ],
        }
    )
    mem.append({"role": "tool", "content": "已记录发现 #1：命令注入"})
    mem.append({"role": "assistant", "content": "继续"})
    mem.append({"role": "tool", "content": "ok"})
    prompts: list[str] = []
    mem.compress(lambda p: prompts.append(p) or "摘要")
    assert "命令注入" in prompts[0]
    assert "description" in prompts[0]
    assert "submit_finding" in prompts[0]


def test_loop_attaches_pinned_state_on_compression():
    """端到端：loop 把外部钉住块交给 WorkingMemory，且压缩事件可观测。"""
    call = ToolCall(
        id="t1",
        name="echo",
        arguments={"text": "x" * 100},
        raw_arguments=json.dumps({"text": "x" * 100}),
    )
    responses = [LLMResponse(content="", tool_calls=[call]) for _ in range(2)]
    responses.append(LLMResponse(content="完成"))
    client = MockLLMClient(responses)
    memory = WorkingMemory(active_window_turns=1)
    events: list[tuple[str, dict]] = []
    loop = AgentLoop(
        client=client,
        registry=_registry(),
        memory=memory,
        budget=TokenBudget(BudgetSettings(context_window=1500)),
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=5),
        config=LoopConfig(),
        compressor=lambda _p: "推理摘要",
        pinned_state=lambda: "已提交发现 #1：a.py:4 命令注入",
        on_event=lambda e, p: events.append((e, p)),
    )
    result = loop.run(
        [
            {"role": "system", "content": "x" * 3000},
            {"role": "user", "content": "任务"},
        ]
    )
    assert result.compress_events >= 1
    assert "a.py:4 命令注入" in memory.pinned
    compress_payload = next(p for e, p in events if e == "compress")
    assert compress_payload["pinned_chars"] > 0


# ---------------------------------------------------------------- 4. 去重语义


def _finding(line: int, category: Category, title: str, source: Source) -> Finding:
    return Finding(
        file="a.py",
        line_start=line,
        line_end=line,
        category=category,
        severity=Severity.MAJOR,
        title=title,
        source=source,
        rule_id="R1",
    )


def test_same_issue_ignores_category():
    a = _finding(10, Category.BUG, "命令注入", Source.LLM)
    b = _finding(11, Category.SECURITY, "命令注入", Source.LLM)
    assert same_issue(a, b)


def test_same_issue_keeps_distinct_titles():
    a = _finding(10, Category.BUG, "空指针解引用", Source.AST_RULE)
    b = _finding(11, Category.BUG, "资源未释放", Source.AST_RULE)
    assert not same_issue(a, b)


def test_bucket_boundary_lines_are_clustered():
    """旧版 line//3 让第 2、3 行相邻却分到不同桶，跨来源重复漏合并。"""
    a = _finding(2, Category.SECURITY, "B307 blacklist", Source.BANDIT)
    b = _finding(3, Category.SECURITY, "使用了 eval()", Source.AST_RULE)
    result = dedup_findings([a, b])
    assert len(result) == 1
    assert result[0].source is Source.AST_RULE


def test_far_lines_stay_separate():
    a = _finding(10, Category.SECURITY, "B307 blacklist", Source.BANDIT)
    b = _finding(14, Category.SECURITY, "使用了 eval()", Source.AST_RULE)
    assert len(dedup_findings([a, b])) == 2


def test_location_key_carries_no_category_or_bucket():
    f = _finding(2, Category.BUG, "x", Source.LLM)
    assert f.location_key == "a.py:2"
