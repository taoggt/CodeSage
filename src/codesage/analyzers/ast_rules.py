"""自研 AST 规则引擎（零 Token 的核心资产）。

设计说明：
- 每条规则实现 `check(tree, ctx) -> list[Finding]`，可独立测试（正反例成对）；
- NodeRule 基类提供"节点类型 + 单节点检查"的模板，覆盖大多数规则；
- RuleContext 封装源码行与 Finding 构造，规则实现只关心检测逻辑；
- 规则内部异常被捕获并跳过——一条坏规则不能拖垮整个审查。

规则产出的是"候选发现"：高确定性规则（如裸 except）可直接进入报告；
启发式规则（如未使用 with）标记较低 confidence，由 LLM 复核确认。
"""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from codesage.analyzers.base import is_test_file
from codesage.core.languages import Language, language_for_path
from codesage.core.models import Category, Finding, Severity, Source

# ---------------------------------------------------------------- 基础设施


@dataclass
class RuleContext:
    """规则执行上下文。"""

    filename: str
    source_lines: list[str] = field(default_factory=list)

    def line_text(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def make_finding(
        self,
        node: ast.AST,
        *,
        rule_id: str,
        title: str,
        description: str = "",
        suggestion: str = "",
        severity: Severity = Severity.MINOR,
        category: Category = Category.QUALITY,
        confidence: float = 1.0,
        include_evidence: bool = True,
    ) -> Finding:
        lineno = int(getattr(node, "lineno", 1) or 1)
        endline = int(getattr(node, "end_lineno", lineno) or lineno)
        evidence = self.line_text(lineno) if include_evidence else ""
        return Finding(
            file=self.filename,
            line_start=lineno,
            line_end=max(lineno, endline),
            category=category,
            severity=severity,
            title=title,
            description=description,
            suggestion=suggestion,
            evidence=evidence[:200],
            confidence=confidence,
            source=Source.AST_RULE,
            rule_id=rule_id,
        )


class Rule(ABC):
    """规则基类。"""

    id: str = "rule"
    title: str = ""

    @abstractmethod
    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        """检查语法树，返回发现列表。"""


class NodeRule(Rule):
    """按节点类型遍历的规则模板。

    子类声明 node_types 并实现 visit_node(node, ctx)，
    引擎负责遍历与分发。若规则需要整树上下文（如 parent 链），
    可直接重写 check()。
    """

    node_types: tuple[type, ...] = ()

    @abstractmethod
    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        """检查单个节点。"""

    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, self.node_types):
                findings.extend(self.visit_node(node, ctx))
        return findings

def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """构建子节点 -> 父节点映射（用 id() 作为键，避免节点不可哈希问题）。"""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_string_like(node: ast.AST) -> bool:
    """判断表达式是否涉及字符串字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_like(node.left) or _is_string_like(node.right)
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    return False


# ---------------------------------------------------------------- 规则实现


class MutableDefaultArgRule(NodeRule):
    """可变默认参数：def f(x=[]) —— 经典陷阱，默认值在函数定义时只求值一次。"""

    id = "CG001"
    title = "可变默认参数"
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    _MUTABLE_LITERAL = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
    _MUTABLE_FACTORY = ("list", "dict", "set")

    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        findings: list[Finding] = []
        for default in defaults:
            is_mutable = isinstance(default, self._MUTABLE_LITERAL)
            if not is_mutable and isinstance(default, ast.Call):
                if isinstance(default.func, ast.Name) and default.func.id in self._MUTABLE_FACTORY:
                    is_mutable = True
            if is_mutable:
                findings.append(
                    ctx.make_finding(
                        default,
                        rule_id=self.id,
                        title=f"可变默认参数：{node.name}()",
                        description=(
                            "Python 的默认参数只在函数定义时求值一次，"
                            "可变默认值会被所有调用共享，导致跨调用的状态污染。"
                        ),
                        suggestion="改用 None 作默认值，函数内部按需创建：`if x is None: x = []`",
                        severity=Severity.MAJOR,
                        category=Category.BUG,
                    )
                )
        return findings


class BareExceptRule(NodeRule):
    """裸 except：会吞掉 KeyboardInterrupt / SystemExit 等，且掩盖真实错误。"""

    id = "CG002"
    title = "裸 except"
    node_types = (ast.ExceptHandler,)

    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        if node.type is not None:
            return []
        return [
            ctx.make_finding(
                node,
                rule_id=self.id,
                title="裸 except（except:）捕获全部异常",
                description=(
                    "裸 except 会捕获包括 KeyboardInterrupt、SystemExit 在内的所有异常，"
                    "可能掩盖真正的错误，也妨碍程序正常退出。"
                ),
                suggestion="捕获具体异常类型，如 `except ValueError as exc:`；确需兜底时用 `except Exception:`",
                severity=Severity.MAJOR,
                category=Category.BUG,
            )
        ]


class ExceptPassRule(NodeRule):
    """异常吞没：except 块里只有 pass —— 错误被静默丢弃。"""

    id = "CG003"
    title = "异常被静默吞没"
    node_types = (ast.ExceptHandler,)

    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        body = node.body
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            return [
                ctx.make_finding(
                    node,
                    rule_id=self.id,
                    title="异常被静默吞没（except: pass）",
                    description="异常发生后被直接忽略，问题将在更远的地方以更难排查的方式暴露。",
                    suggestion="至少记录日志（logging.exception），或明确注释为何可以忽略",
                    severity=Severity.MAJOR,
                    category=Category.BUG,
                )
            ]
        return []


class EqNoneRule(NodeRule):
    """用 == 比较 None：应使用 is None（None 是单例，is 更快且语义正确）。"""

    id = "CG004"
    title = "None 比较方式不当"
    node_types = (ast.Compare,)

    def visit_node(self, node: ast.Compare, ctx: RuleContext) -> list[Finding]:
        operands = [node.left, *node.comparators]
        for op in node.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            for operand in operands:
                if isinstance(operand, ast.Constant) and operand.value is None:
                    op_text = "==" if isinstance(op, ast.Eq) else "!="
                    return [
                        ctx.make_finding(
                            node,
                            rule_id=self.id,
                            title=f"使用 {op_text} 比较 None",
                            description="None 是单例对象，身份比较（is / is not）更准确也更快。",
                            suggestion="改为 `is None` / `is not None`",
                            severity=Severity.MINOR,
                            category=Category.QUALITY,
                        )
                    ]
        return []


class UnclosedOpenRule(Rule):
    """open() 未使用 with：资源泄漏候选（启用 LLM 复核的典型场景）。

    该规则只能发现"未在 with 内"的 open 调用；
    调用方是否手动 close 需要语义确认，因此置信度设为 0.6，
    由 Agent 的 LLM 环节复核后决定保留或丢弃。
    """

    id = "CG005"
    title = "文件句柄未使用 with"

    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        parents = _build_parent_map(tree)
        findings: list[Finding] = []
        for node in ast.walk(tree):
            if not self._is_open_call(node):
                continue
            if self._inside_with(node, parents):
                continue
            findings.append(
                ctx.make_finding(
                    node,
                    rule_id=self.id,
                    title="open() 未使用 with 语句",
                    description=(
                        "open() 返回的文件句柄未在 with 块中管理，"
                        "若异常路径未 close 会导致资源泄漏。"
                        "（若已手动 close，请在复核时忽略本条）"
                    ),
                    suggestion="改用 `with open(path) as f:`，异常路径也能保证关闭",
                    severity=Severity.MINOR,
                    category=Category.BUG,
                    confidence=0.6,
                )
            )
        return findings

    @staticmethod
    def _is_open_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        return isinstance(func, ast.Name) and func.id == "open"

    @staticmethod
    def _inside_with(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        cur: ast.AST | None = node
        while cur is not None and id(cur) in parents:
            parent = parents[id(cur)]
            if isinstance(parent, (ast.With, ast.AsyncWith)):
                return True
            cur = parent
        return False


class MissingDocstringRule(NodeRule):
    """缺少 docstring：公共 API 记 MINOR，私有符号记 INFO（避免噪声）。

    测试代码整体豁免：测试函数（test_*）与测试辅助函数不写 docstring
    是行业惯例，报告它们只会淹没真实问题。
    """

    id = "CG006"
    title = "缺少文档字符串"
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        if is_test_file(ctx.filename):
            return []
        name = node.name
        if name.startswith("__") and name.endswith("__"):
            return []  # 魔术方法豁免（行业惯例）
        if ast.get_docstring(node) is not None:
            return []
        is_public = not name.startswith("_")
        return [
            ctx.make_finding(
                node,
                rule_id=self.id,
                title=f"{'公共' if is_public else '私有'}符号缺少 docstring：{name}",
                description="缺少文档会影响可维护性与 IDE 提示质量。",
                suggestion='补充一行式 docstring："""一句话说明职责。"""',
                severity=Severity.MINOR if is_public else Severity.INFO,
                category=Category.QUALITY,
                confidence=0.9,
            )
        ]


class StrConcatInLoopRule(NodeRule):
    """循环内字符串拼接：str 不可变，每次 += 都会创建新对象，O(n²)。"""

    id = "CG007"
    title = "循环内字符串拼接"
    node_types = (ast.For, ast.While)

    def visit_node(self, loop: ast.AST, ctx: RuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(loop):
            if (
                isinstance(node, ast.AugAssign)
                and isinstance(node.op, ast.Add)
                and _is_string_like(node.value)
            ):
                findings.append(
                    ctx.make_finding(
                        node,
                        rule_id=self.id,
                        title="循环内使用 += 拼接字符串",
                        description="字符串不可变，循环内累加会反复创建新对象，复杂度 O(n²)。",
                        suggestion='改为收集到列表再 "".join(parts)',
                        severity=Severity.MINOR,
                        category=Category.PERFORMANCE,
                        confidence=0.8,
                    )
                )
        return findings


class ListMembershipRule(NodeRule):
    """用 in 列表字面量做成员判断：应为 set 以获得 O(1) 查找。"""

    id = "CG008"
    title = "列表成员检查效率低"
    node_types = (ast.Compare,)

    def visit_node(self, node: ast.Compare, ctx: RuleContext) -> list[Finding]:
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if isinstance(comparator, (ast.List, ast.Tuple)) and len(comparator.elts) >= 3:
                return [
                    ctx.make_finding(
                        node,
                        rule_id=self.id,
                        title="在列表字面量上做成员检查",
                        description="`x in [a, b, c, ...]` 是线性查找；集合是哈希查找 O(1)。",
                        suggestion="改为集合字面量 `x in {a, b, c, ...}`（元素需可哈希）",
                        severity=Severity.INFO,
                        category=Category.PERFORMANCE,
                        confidence=0.85,
                    )
                ]
        return []


class RangeLenLoopRule(NodeRule):
    """for i in range(len(x))：应使用 enumerate 或直接迭代元素。"""

    id = "CG009"
    title = "range(len(...)) 迭代"
    node_types = (ast.For,)

    def visit_node(self, node: ast.For, ctx: RuleContext) -> list[Finding]:
        it = node.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range"):
            return []
        if len(it.args) != 1:
            return []
        arg = it.args[0]
        if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "len"):
            return []
        if not isinstance(node.target, ast.Name):
            return []
        target_name = node.target.id
        uses_index = any(
            isinstance(n, ast.Subscript)
            and isinstance(n.slice, ast.Name)
            and n.slice.id == target_name
            for n in ast.walk(node)
        )
        suggestion = (
            "需要索引和元素：改用 `for i, item in enumerate(x):`"
            if uses_index
            else "只需要元素：直接 `for item in x:` 迭代"
        )
        return [
            ctx.make_finding(
                node,
                rule_id=self.id,
                title="使用 range(len(x)) 迭代",
                description="这种写法可读性差，且丧失了 Python 惯用迭代的优势。",
                suggestion=suggestion,
                severity=Severity.INFO,
                category=Category.BEST_PRACTICE,
                confidence=0.95,
            )
        ]


class BroadExceptRule(NodeRule):
    """过宽的 except Exception：建议精细化捕获（区别于裸 except，级别较低）。"""

    id = "CG010"
    title = "过宽的异常捕获"
    node_types = (ast.ExceptHandler,)

    def visit_node(self, node: ast.ExceptHandler, ctx: RuleContext) -> list[Finding]:
        exc_type = node.type
        if isinstance(exc_type, ast.Name) and exc_type.id == "Exception":
            return [
                ctx.make_finding(
                    node,
                    rule_id=self.id,
                    title="捕获了过宽的 Exception",
                    description=(
                        "捕获 Exception 会连带吞掉预期外的错误。"
                        "（作为顶层兜底 + 记录日志是可接受的模式，请结合实际判断）"
                    ),
                    suggestion="尽量捕获具体异常；顶层兜底时务必记录日志并保留堆栈",
                    severity=Severity.MINOR,
                    category=Category.QUALITY,
                    confidence=0.7,
                )
            ]
        return []


class EvalExecRule(NodeRule):
    """eval / exec：任意代码执行风险。"""

    id = "CG011"
    title = "eval / exec 使用"
    node_types = (ast.Call,)

    def visit_node(self, node: ast.Call, ctx: RuleContext) -> list[Finding]:
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
            return [
                ctx.make_finding(
                    node,
                    rule_id=self.id,
                    title=f"使用了 {func.id}()",
                    description="eval/exec 执行任意代码，输入可控时构成代码注入漏洞。",
                    suggestion=(
                        "解析字面量用 ast.literal_eval；动态逻辑改用具名函数或字典分派"
                    ),
                    severity=Severity.MAJOR,
                    category=Category.SECURITY,
                )
            ]
        return []


class HardcodedSecretRule(NodeRule):
    """硬编码密钥/口令：从变量命名 + 字符串字面量模式识别。"""

    id = "CG012"
    title = "硬编码敏感信息"
    node_types = (ast.Assign,)

    _HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "access_key")
    _PLACEHOLDERS = {"password", "secret", "token", "changeme", "example", "placeholder", "xxx"}

    def visit_node(self, node: ast.Assign, ctx: RuleContext) -> list[Finding]:
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any(hint in n.lower() for n in names for hint in self._HINTS):
            return []
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            return []
        text = value.value.strip()
        if len(text) < 6:
            return []
        if text.lower() in self._PLACEHOLDERS or text.lower().startswith(("your", "test", "<")):
            return []
        var_name = next(n for n in names if any(h in n.lower() for h in self._HINTS))
        return [
            ctx.make_finding(
                node,
                rule_id=self.id,
                title=f"疑似硬编码敏感信息：{var_name}",
                description="源码中的密钥会随版本库扩散，一旦泄露需整体轮换。",
                suggestion="改为从环境变量或密钥管理服务读取（如 os.environ / Secret Manager）",
                severity=Severity.MAJOR,
                category=Category.SECURITY,
                confidence=0.75,
            )
        ]


class SubprocessShellRule(NodeRule):
    """命令注入风险：subprocess(..., shell=True) / os.system / os.popen。"""

    id = "CG013"
    title = "命令注入风险"
    node_types = (ast.Call,)

    def visit_node(self, node: ast.Call, ctx: RuleContext) -> list[Finding]:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
            if func.attr in ("system", "popen"):
                return [
                    ctx.make_finding(
                        node,
                        rule_id=self.id,
                        title=f"使用了 os.{func.attr}()",
                        description="os.system / os.popen 经 shell 执行命令，拼接外部输入会构成命令注入。",
                        suggestion="改用 subprocess.run([...], shell=False) 传参数列表",
                        severity=Severity.MAJOR,
                        category=Category.SECURITY,
                    )
                ]
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    return [
                        ctx.make_finding(
                            node,
                            rule_id=self.id,
                            title="subprocess 使用 shell=True",
                            description="shell=True 时命令经 shell 解释，参数拼接存在注入风险。",
                            suggestion="去掉 shell=True，以列表传递命令与参数",
                            severity=Severity.MAJOR,
                            category=Category.SECURITY,
                            confidence=0.9,
                        )
                    ]
        return []


# ---------------------------------------------------------------- 引擎


def default_rules() -> list[Rule]:
    """默认规则集（13 条，覆盖质量 / bug / 性能 / 安全四个维度）。"""
    return [
        MutableDefaultArgRule(),
        BareExceptRule(),
        ExceptPassRule(),
        EqNoneRule(),
        UnclosedOpenRule(),
        MissingDocstringRule(),
        StrConcatInLoopRule(),
        ListMembershipRule(),
        RangeLenLoopRule(),
        BroadExceptRule(),
        EvalExecRule(),
        HardcodedSecretRule(),
        SubprocessShellRule(),
    ]


RULE_IDS = [
    "CG001", "CG002", "CG003", "CG004", "CG005", "CG006", "CG007",
    "CG008", "CG009", "CG010", "CG011", "CG012", "CG013",
]


class RuleEngine:
    """规则引擎：单次解析，多规则执行。

    v2 起它是一个"语言专属引擎"：只处理 Python。语言守卫（run 的首行）不是
    防御性编程洁癖，而是**必须**——`ast.parse` 遇到 Java 源码会抛 SyntaxError，
    旧行为会据此产出 `CG000`「语法错误 MAJOR」并严重扣分，把"不支持 Java"
    伪装成"你的 Java 代码有语法错误"。
    """

    name = "ast_rule"
    language = Language.PYTHON
    languages: tuple[Language, ...] = (Language.PYTHON,)

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = rules if rules is not None else default_rules()

    def supports(self, language: Language) -> bool:
        return language in self.languages

    def supports_any(self, languages: object) -> bool:
        return any(self.supports(lang) for lang in languages)  # type: ignore[union-attr]

    def is_available(self) -> bool:
        """标准库 ast 恒可用，无需探测。"""
        return True

    def run(self, filename: str, source: str) -> list[Finding]:
        """解析源码并执行全部规则。

        - 非 Python 文件：直接返回空（语言守卫）；
        - 语法错误：报告一条 MAJOR（bug 类），后续规则无法执行；
        - 单条规则异常：跳过该规则（不影响其他规则）。
        """
        detected = language_for_path(filename)
        if detected is not None and detected is not Language.PYTHON:
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [
                Finding(
                    file=filename,
                    line_start=exc.lineno or 1,
                    line_end=exc.lineno or 1,
                    category=Category.BUG,
                    severity=Severity.MAJOR,
                    title=f"语法错误：{exc.msg}",
                    description=f"文件无法解析（{exc.__class__.__name__}），请先修复语法。",
                    source=Source.AST_RULE,
                    rule_id="CG000",
                    language=Language.PYTHON.value,
                )
            ]

        ctx = RuleContext(filename=filename, source_lines=source.splitlines())
        findings: list[Finding] = []
        for rule in self.rules:
            try:
                findings.extend(rule.check(tree, ctx))
            except Exception:  # noqa: BLE001 - 单条规则失败不影响整体
                continue
        return findings
