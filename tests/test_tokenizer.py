"""Token 估算器测试。"""

from codesage.llm.tokenizer import HeuristicEstimator, get_estimator


def test_estimate_empty():
    est = HeuristicEstimator()
    assert est.estimate("") == 0


def test_estimate_cjk():
    est = HeuristicEstimator()
    # 4 个 CJK 字符 ≈ 4 token（+1 基准）
    result = est.estimate("你好世界")
    assert 4 <= result <= 6


def test_estimate_ascii():
    est = HeuristicEstimator()
    # 35 个 ASCII 字符 ≈ 10 token
    result = est.estimate("x" * 35)
    assert 9 <= result <= 12


def test_estimate_messages_includes_overhead():
    est = HeuristicEstimator()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello world"},
    ]
    assert est.estimate_messages(messages) > est.estimate("sys") + est.estimate(
        "hello world"
    )


def test_estimate_messages_with_tool_calls():
    est = HeuristicEstimator()
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                }
            ],
        }
    ]
    assert est.estimate_messages(messages) > 10  # tool_calls 的 JSON 体积被计入


def test_get_estimator_default_is_heuristic():
    est = get_estimator(prefer_tiktoken=False)
    assert isinstance(est, HeuristicEstimator)
