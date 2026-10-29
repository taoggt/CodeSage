"""工具注册表：@tool 装饰器 + JSON Schema 自动生成 + 统一执行入口。

设计要点：
- Schema 从函数签名与类型注解反射生成，保证"实现与声明"不会脱节
  （手写 schema 是常见 bug 来源）；
- 参数描述从 docstring 的 Args 段解析，写好 docstring 即可获得好 schema；
- execute() 永不抛出异常：工具错误被转成 "ERROR: ..." 文本回填给模型，
  让模型自我纠正（这是 Agent 健壮性的关键设计）。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

_TYPE_MAP: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """把 Python 类型注解转成 JSON Schema 片段。"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    # list[str] / list[int] 等泛型
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", ())
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if annotation in _TYPE_MAP:
        return {"type": _TYPE_MAP[annotation]}
    return {"type": "string"}


def _parse_docstring(docstring: str | None) -> tuple[str, dict[str, str]]:
    """解析 docstring：返回 (摘要, {参数名: 描述})。

    支持的 Args 段格式：
        Args:
            path: 文件路径（相对工作区）
            start_line: 起始行号
    """
    if not docstring:
        return "", {}
    lines = docstring.strip().splitlines()
    summary = lines[0].strip() if lines else ""

    params: dict[str, str] = {}
    in_args = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "参数:"):
            in_args = True
            continue
        if in_args:
            if stripped.endswith(":") and not line.startswith((" ", "\t")):
                break  # 进入下一个段落（Returns 等）
            if ":" in stripped:
                name, desc = stripped.split(":", 1)
                name = name.strip().lstrip("*")
                if name.isidentifier():
                    params[name] = desc.strip()
    return summary, params


@dataclass
class Tool:
    """一个可被模型调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any] = field(repr=False)

    def schema(self) -> dict[str, Any]:
        """OpenAI function calling 的 tools 条目格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __call__(self, **kwargs: Any) -> Any:
        return self.func(**kwargs)


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    """装饰器：把函数标记为工具（只挂元数据，不产生全局副作用）。

    用法：
        @tool
        def read_file(path: str, start_line: int = 1) -> str:
            '''读取文件内容。

            Args:
                path: 文件路径
                start_line: 起始行号
            '''
            ...
    """
    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        f._is_codesage_tool = True  # type: ignore[attr-defined]
        f._tool_name = name or f.__name__  # type: ignore[attr-defined]
        if description is not None:
            f._tool_description = description  # type: ignore[attr-defined]
        return f

    if func is not None:
        return decorate(func)
    return decorate


class ToolRegistry:
    """工具注册与执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        """显式注册一个工具函数（可在 @tool 元数据之上覆盖名称/描述）。"""
        doc = inspect.getdoc(func) or ""
        summary, param_docs = _parse_docstring(doc)

        tool_name = name or getattr(func, "_tool_name", None) or func.__name__
        tool_desc = (
            description
            or getattr(func, "_tool_description", None)
            or summary
            or tool_name
        )

        properties: dict[str, Any] = {}
        required: list[str] = []
        signature = inspect.signature(func)
        for pname, param in signature.parameters.items():
            if pname in ("self", "cls"):
                continue
            schema = _annotation_to_schema(param.annotation)
            schema["description"] = param_docs.get(pname, pname)
            properties[pname] = schema
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        registered = Tool(
            name=tool_name,
            description=tool_desc,
            parameters=parameters,
            func=func,
        )
        self._tools[tool_name] = registered
        return registered

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        max_result_chars: int = 6000,
    ) -> str:
        """执行工具：任何错误都转成可读文本返回（不向上抛出）。

        错误回填是刻意设计：模型看到错误信息后会调整策略（自我纠正），
        比让整个循环崩溃更健壮。
        """
        tool_obj = self._tools.get(name)
        if tool_obj is None:
            return f"ERROR: 未知工具 '{name}'。可用工具：{', '.join(self.names())}"

        # 过滤多余参数（模型偶尔会带上 schema 外的键）
        valid = set(tool_obj.parameters.get("properties", {}))
        clean_args = {k: v for k, v in (arguments or {}).items() if k in valid}

        try:
            result = tool_obj.func(**clean_args)
        except TypeError as exc:
            missing = tool_obj.parameters.get("required", [])
            return f"ERROR: 参数不匹配（{exc}）。required 参数：{missing}"
        except Exception as exc:  # noqa: BLE001 - 有意兜底，转文本回填
            return f"ERROR: 工具 '{name}' 执行失败：{exc.__class__.__name__}: {exc}"

        text = self._stringify(result)
        if len(text) > max_result_chars:
            text = text[:max_result_chars] + f"\n...(结果已截断，共 {len(text)} 字符)"
        return text

    @staticmethod
    def _stringify(result: Any) -> str:
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, indent=1, default=str)
        except (TypeError, ValueError):
            return str(result)
