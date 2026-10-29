"""工具注册表测试：schema 生成 / 执行容错 / 参数过滤。"""

from codesage.agent.registry import ToolRegistry, tool


def test_schema_generation_from_signature():
    registry = ToolRegistry()

    @tool
    def sample(path: str, count: int = 3, flag: bool = False) -> str:
        """示例工具说明。

        Args:
            path: 文件路径
            count: 数量
        """
        return path

    registered = registry.register(sample)
    function = registered.schema()["function"]

    assert function["name"] == "sample"
    params = function["parameters"]
    assert params["properties"]["path"]["type"] == "string"
    assert params["properties"]["count"]["type"] == "integer"
    assert params["properties"]["flag"]["type"] == "boolean"
    assert params["properties"]["path"]["description"] == "文件路径"
    assert params["required"] == ["path"]  # 只有无默认值的参数是 required


def test_list_annotation_maps_to_array():
    registry = ToolRegistry()

    @tool
    def multi(files: list[str]) -> str:
        """多文件工具。"""
        return ",".join(files)

    schema = registry.register(multi).schema()["function"]
    assert schema["parameters"]["properties"]["files"]["type"] == "array"
    assert schema["parameters"]["properties"]["files"]["items"]["type"] == "string"


def test_execute_unknown_tool_returns_error_text():
    registry = ToolRegistry()
    output = registry.execute("ghost", {})
    assert output.startswith("ERROR")
    assert "未知工具" in output


def test_execute_filters_extra_arguments():
    registry = ToolRegistry()

    @tool
    def one_arg(value: str) -> str:
        """单参数工具。

        Args:
            value: 值
        """
        return f"ok:{value}"

    registry.register(one_arg)
    # 模型偶尔会带上 schema 之外的参数——应被过滤而不是报错
    output = registry.execute("one_arg", {"value": "a", "extra": "b"})
    assert output == "ok:a"


def test_execute_truncates_long_output():
    registry = ToolRegistry()

    @tool
    def big() -> str:
        """返回大文本。"""
        return "x" * 10000

    registry.register(big)
    output = registry.execute("big", {}, max_result_chars=100)
    assert len(output) < 200
    assert "截断" in output


def test_execute_wraps_exception():
    registry = ToolRegistry()

    @tool
    def failing() -> str:
        """失败工具。"""
        raise ValueError("坏输入")

    registry.register(failing)
    output = registry.execute("failing", {})
    assert output.startswith("ERROR")
    assert "ValueError" in output
