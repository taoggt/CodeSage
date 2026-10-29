"""三区工作记忆测试：分区组装 / 压缩触发条件 / 压缩失败保护。"""

from codesage.memory.working import WorkingMemory


def fill_turns(mem: WorkingMemory, turns: int) -> None:
    """填充 n 轮对话（每轮 = assistant + tool 结果）。"""
    for i in range(turns):
        mem.append({"role": "assistant", "content": f"a{i}"})
        mem.append({"role": "tool", "content": f"t{i}"})


def test_build_includes_frozen_then_active():
    mem = WorkingMemory(active_window_turns=2)
    mem.freeze([{"role": "system", "content": "sys"}])
    mem.append({"role": "user", "content": "u1"})
    built = mem.build()
    assert built[0] == {"role": "system", "content": "sys"}
    assert built[-1]["content"] == "u1"


def test_compress_requires_more_turns_than_window():
    mem = WorkingMemory(active_window_turns=2)
    fill_turns(mem, 2)  # 恰好 window 轮
    assert mem.compress(lambda p: "摘要") is None
    assert mem.compressed is False


def test_compress_removes_old_and_keeps_recent():
    mem = WorkingMemory(active_window_turns=1)
    fill_turns(mem, 3)  # 3 轮

    event = mem.compress(lambda p: "结构化摘要")
    assert event is not None
    assert event.removed_messages == 4  # 前 2 轮（2 × assistant+tool）
    assert event.kept_messages == 2     # 最后 1 轮
    assert mem.summary == "结构化摘要"

    built = mem.build()
    # 冻结区 + 摘要 + 活跃区
    summary_msgs = [m for m in built if "摘要" in str(m.get("content", ""))]
    assert summary_msgs
    assert mem.active_count == 2


def test_compress_prompt_contains_previous_summary():
    mem = WorkingMemory(active_window_turns=1)
    fill_turns(mem, 3)
    mem.compress(lambda p: "第一次摘要")
    prompts: list[str] = []

    fill_turns(mem, 2)
    mem.compress(lambda p: prompts.append(p) or "第二次摘要")

    assert prompts and "第一次摘要" in prompts[0]  # 旧摘要参与了二次压缩
    assert mem.summary == "第二次摘要"


def test_compress_failure_keeps_state():
    mem = WorkingMemory(active_window_turns=1)
    fill_turns(mem, 3)

    assert mem.compress(lambda p: "") is None  # 压缩器返回空 = 失败
    assert mem.compressed is False
    assert mem.active_count == 6  # 状态原样保留（信息优先于成本）


def test_compress_exception_keeps_state():
    mem = WorkingMemory(active_window_turns=1)
    fill_turns(mem, 3)

    def exploding(prompt: str) -> str:
        raise RuntimeError("LLM 挂了")

    assert mem.compress(exploding) is None
    assert mem.active_count == 6
