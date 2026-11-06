"""tree-sitter-java 封装：语法树、符号大纲、圈复杂度。

为什么用 tree-sitter 而不是 javalang 或 JVM 工具
------------------------------------------------
- javalang 只支持到 Java 8 语法（record / sealed / var 全不认），且项目已停更；
- Checkstyle / PMD / SpotBugs 需要 JDK 与 jar 管理，会让"审 Java"变成"先配环境"；
- tree-sitter 有预编译 wheel（Windows 也无需编译器），只做语法层，
  且未来加 Go / TS 可以复用同一套封装。

设计边界（诚实标注，写进文档）
------------------------------
- **只做语法层**：不解析类型、不建符号表、不做跨文件引用。因此
  `@Transactional` 自调用失效、NPE 数据流、未使用 import、循环依赖这几类
  问题在阶段一做不到——它们需要语义/链接阶段的信息，留给阶段二的 JVM 工具。
- **语法错误不产出发现**：Java 半成品文件很常见（编辑中的文件、生成模板）。
  Python 侧遇到 SyntaxError 会报 `CG000`，但 Java 侧我们不这么做——
  "解析失败"与"代码有问题"是两件事，混在一起会污染报告。
- 超过 MAX_PARSE_BYTES 不解析：防单文件吃满内存与耗时。

版本兼容
--------
tree-sitter 的 Python binding 在 0.22 → 0.26 之间改过 Language/Parser 的构造方式
（`Language(ptr, name)` 变成 `Language(capsule)`，`Parser().language = x` 变成
`Parser(x)`）。这里用 try 链兜住，任何一环失败都退回"不可用"而不是抛异常——
分析器不可用是正常状态（没装依赖），不该让审查崩掉。
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from typing import Any, Iterator

# 单文件解析上限：2MB 的 Java 源码已经很夸张了，超过基本是生成代码
MAX_PARSE_BYTES = 2_000_000

# 类型声明节点（class / interface / enum / record / 注解类型）
TYPE_DECLARATION_TYPES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
)

METHOD_TYPES = frozenset({"method_declaration", "constructor_declaration"})

# 圈复杂度的决策点（McCC 的常用近似）：每个分支 +1，基准 1
_COMPLEXITY_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "ternary_expression",
    }
)
_SWITCH_LABEL_TYPES = frozenset({"switch_label", "switch_rule"})
_LOGICAL_OPERATORS = frozenset({"&&", "||"})

_UNSET = object()
_language_cache: Any = _UNSET
_parser_cache: Any = _UNSET


# ---------------------------------------------------------------- 可用性


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def is_available() -> bool:
    """依赖是否齐备（缺任何一个都视为不可用，调用方降级）。"""
    return _module_available("tree_sitter") and _module_available("tree_sitter_java")


def _build_language() -> Any:
    """构造 Java Language 对象（跨 binding 版本）。"""
    import tree_sitter
    import tree_sitter_java

    raw = tree_sitter_java.language()
    attempts = (
        lambda: tree_sitter.Language(raw),            # >= 0.23（PyCapsule）
        lambda: tree_sitter.Language(raw, "java"),    # <= 0.22（指针 + 名称）
    )
    for attempt in attempts:
        try:
            return attempt()
        except (TypeError, ValueError, AttributeError):
            continue
    return None


def _build_parser() -> Any:
    """构造 Parser（跨 binding 版本）。"""
    import tree_sitter

    language = _load_language()
    if language is None:
        return None
    try:
        return tree_sitter.Parser(language)   # >= 0.24
    except (TypeError, ValueError):
        pass
    try:
        parser = tree_sitter.Parser()         # <= 0.23
        parser.language = language
        return parser
    except (AttributeError, TypeError, ValueError):
        return None


def _load_language() -> Any:
    global _language_cache
    if _language_cache is _UNSET:
        if not is_available():
            _language_cache = None
        else:
            try:
                _language_cache = _build_language()
            except Exception:  # noqa: BLE001 - 依赖损坏也按不可用处理
                _language_cache = None
    return _language_cache


def _get_parser() -> Any:
    global _parser_cache
    if _parser_cache is _UNSET:
        try:
            _parser_cache = _build_parser()
        except Exception:  # noqa: BLE001
            _parser_cache = None
    return _parser_cache


def reset_cache() -> None:
    """清空语言/解析器缓存（测试用：模拟"依赖不存在"后恢复）。"""
    global _language_cache, _parser_cache
    _language_cache = _UNSET
    _parser_cache = _UNSET


# ---------------------------------------------------------------- 解析


@dataclass(frozen=True)
class JavaTree:
    """解析结果：语法树 + 原始字节。

    为什么要自己持有字节：tree-sitter 的节点只给字节偏移，取源码文本要么靠
    `node.text`（不同版本行为有差异），要么自己切片。自己切片最稳，也能
    避免把 bytes 反复编解码。
    """

    tree: Any
    source_bytes: bytes
    source_text: str

    @property
    def root(self) -> Any:
        return self.tree.root_node

    @property
    def has_error(self) -> bool:
        return bool(self.root.has_error)

    def text(self, node: Any) -> str:
        return self.source_bytes[node.start_byte : node.end_byte].decode(
            "utf-8", "replace"
        )

    def lines(self) -> list[str]:
        return self.source_text.splitlines()


def parse_java(source: str, *, allow_errors: bool = False) -> JavaTree | None:
    """解析 Java 源码。

    返回 None 的三种情况（调用方一律当作"无发现"，不产出"语法错误"报告）：
    1. 依赖未安装；
    2. 源码超过 MAX_PARSE_BYTES；
    3. 存在语法错误（allow_errors=False 时）。
    """
    if not source:
        return None

    source_bytes = source.encode("utf-8", "replace")
    if len(source_bytes) > MAX_PARSE_BYTES:
        return None

    parser = _get_parser()
    if parser is None:
        return None

    try:
        tree = parser.parse(source_bytes)
    except Exception:  # noqa: BLE001 - 解析器内部错误不应影响审查主流程
        return None
    if tree is None:
        return None

    result = JavaTree(tree=tree, source_bytes=source_bytes, source_text=source)
    if result.has_error and not allow_errors:
        return None
    return result


# ---------------------------------------------------------------- 遍历工具


def row_of(point: Any) -> int:
    """tree-sitter 的坐标是 0-based；这里统一 +1 成报告用的 1-based 行号。

    这一层是**唯一**做 +1 的地方：规则里如果直接用 `node.start_point.row`，
    所有行号会偏 1，评测的行区间匹配会整体失准。
    """
    try:
        return int(point.row)
    except AttributeError:
        return int(point[0])


def line_of(node: Any) -> int:
    return row_of(node.start_point) + 1


def end_line_of(node: Any) -> int:
    return row_of(node.end_point) + 1


def walk(node: Any) -> Iterator[Any]:
    """深度优先遍历全部节点（含无名节点）。

    规则里按类型过滤时用 named 版本更省：`walk_named`。
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def walk_named(node: Any) -> Iterator[Any]:
    """只遍历具名节点（跳过标点、关键字等 unnamed 节点）。"""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def text_of(tree: JavaTree, node: Any) -> str:
    return tree.text(node) if node is not None else ""


def child_text(tree: JavaTree, node: Any, field: str) -> str:
    """取某字段子节点的文本（不存在时返回空串）。"""
    if node is None:
        return ""
    child = node.child_by_field_name(field)
    return tree.text(child) if child is not None else ""


def declared_type(tree: JavaTree, node: Any) -> str:
    """声明节点的类型名（field_declaration / local_variable_declaration / formal_parameter）。"""
    return child_text(tree, node, "type").strip()


def iter_type_declarations(tree: JavaTree) -> Iterator[Any]:
    for node in walk_named(tree.root):
        if node.type in TYPE_DECLARATION_TYPES:
            yield node


def iter_methods(node: Any) -> Iterator[Any]:
    """某个节点内的全部方法/构造器（含嵌套类的）。"""
    for child in walk_named(node):
        if child.type in METHOD_TYPES:
            yield child


def method_name(tree: JavaTree, node: Any) -> str:
    return child_text(tree, node, "name") or "<anonymous>"


def method_signature(tree: JavaTree, node: Any) -> str:
    """方法签名文本："public int compute(int a) throws IllegalStateException"。

    做法：把 body 之前的全部子节点文本拼起来再规范化空白。这样能自动涵盖
    修饰符、泛型返回类型、throws 子句，不需要逐字段枚举（迁移到别的语法版本
    也更稳）。
    """
    parts: list[str] = []
    for child in node.children:
        if child.type in {"block", ";"}:
            break
        parts.append(tree.text(child))
    signature = " ".join(part.strip() for part in parts if part.strip())
    signature = re.sub(r"\s+", " ", signature)
    signature = re.sub(r"\s+([(),])", r"\1", signature)
    signature = re.sub(r"\(\s+", "(", signature)
    return signature.strip()


def type_declaration_header(tree: JavaTree, node: Any) -> str:
    """类型声明头："class UserService extends Base implements IUser"。"""
    header: list[str] = []
    for child in node.children:
        if child.type in {"class_body", "interface_body", "enum_body", "{", ";"}:
            break
        header.append(tree.text(child))
    text = re.sub(r"\s+", " ", " ".join(part.strip() for part in header if part.strip()))
    return text.strip()


# ---------------------------------------------------------------- 符号大纲


def build_java_outline(source: str) -> list[str]:
    """生成符号大纲（供 list_symbols 工具使用，零 Token 的结构化视图）。

    输出形如：
        L12 class UserService implements IUser
          L18 public User findById(Long id) throws NotFoundException
    """
    tree = parse_java(source, allow_errors=True)
    if tree is None:
        return []

    lines: list[str] = []
    for declaration in iter_type_declarations(tree):
        name = child_text(tree, declaration, "name") or "<anonymous>"
        lines.append(f"L{line_of(declaration)} {type_declaration_header(tree, declaration) or name}")
        body = declaration.child_by_field_name("body")
        if body is None:
            continue
        for method in iter_methods(body):
            lines.append(f"  L{line_of(method)} {method_signature(tree, method)}")
    return lines


# ---------------------------------------------------------------- 复杂度


def _is_logical_binary(node: Any) -> bool:
    if node.type != "binary_expression":
        return False
    operator = node.child_by_field_name("operator")
    return operator is not None and operator.type in _LOGICAL_OPERATORS


def _is_non_default_switch_label(tree: JavaTree, node: Any) -> bool:
    if node.type not in _SWITCH_LABEL_TYPES:
        return False
    rendered = tree.text(node).strip()
    return not rendered.startswith("default")


def cyclomatic_complexity(tree: JavaTree, node: Any) -> int:
    """圈复杂度（McCabe 近似）：决策点计数 + 1。

    与 radon 的口径对齐（`radon` 统计 if/for/while/except/with/assert/bool-op），
    这样 Java 与 Python 的报告可以用同一组阈值解释（10 / 20）。

    边界：不进入嵌套的匿名类与 lambda 单独计分——方法内的 lambda 分支算在
    宿主方法上，这是"偏保守"的取舍（宁可高估可维护性成本，也不低估）。
    """
    complexity = 1
    for child in walk_named(node):
        if child.type in _COMPLEXITY_NODE_TYPES:
            complexity += 1
        elif _is_logical_binary(child):
            complexity += 1
        elif _is_non_default_switch_label(tree, child):
            complexity += 1
    return complexity


def enclosing_method(node: Any) -> Any | None:
    """向上找最近的方法/构造器（规则用它定位"这行属于哪个方法"）。"""
    current = node.parent
    while current is not None:
        if current.type in METHOD_TYPES:
            return current
        current = current.parent
    return None


def enclosing_type(node: Any) -> Any | None:
    """向上找最近的类型声明。"""
    current = node.parent
    while current is not None:
        if current.type in TYPE_DECLARATION_TYPES:
            return current
        current = current.parent
    return None


def qualified_method_name(tree: JavaTree, node: Any) -> str:
    """`Class.method` 形式的名字（复杂度报告里比裸方法名可读）。"""
    method = enclosing_method(node) or node
    type_node = enclosing_type(node)
    method_label = method_name(tree, method)
    if type_node is None:
        return method_label
    return f"{child_text(tree, type_node, 'name')}.{method_label}"


def node_kind_label(node: Any) -> str:
    """类型声明的中文类别（大纲/报告用）。"""
    return {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "annotation_type_declaration": "@interface",
    }.get(node.type, node.type)
