"""自研 Java 规则引擎（零 Token 的确定性第一网）。

设计取舍：为什么另起一个引擎，不复用 Python 的 `Rule` / `NodeRule`
-------------------------------------------------------------------
Python 侧规则的 `check(tree, ctx)` 里，`tree` 是 `ast.AST`，节点用 `lineno` /
`body` / `arg.defaults` 这类**语义化**字段；tree-sitter 给的是 CST，节点用
`type` / `child_by_field_name` / 字节偏移。要复用就得写一层 IR 适配（约 150 行），
而 13 条 Python 规则与 187 个测试的断言基础会全部被牵动。

所以选择：**共享输出契约（Finding / Severity / Category / Source），引擎内部解耦**。
两边产出的都是同一个 Finding，合并/去重/评分/报告/GUI 完全不用改。
这比"名义上统一语法树"更诚实——不同语言的语法结构本来就不该被强行抹平。

规则设计原则
------------
- 只做**语法层能确定**的判断。需要类型解析、符号表、跨文件引用的（`@Transactional`
  自调用、NPE 数据流、未使用 import、循环依赖）一律不做，交给 LLM 或阶段二的 JVM 工具。
- 每条规则都能单独测试（正反例成对），单条规则抛异常不影响其他规则。
- 误报控制优先于召回：测试代码里的惯例写法不报（见 `skip_in_tests`），
  纯字面量的场景降级为 INFO 而不是 MAJOR。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterator

from codesage.analyzers import java_parser
from codesage.analyzers.base import is_test_file
from codesage.analyzers.radon_runner import COMPLEXITY_MAJOR, COMPLEXITY_MINOR
from codesage.core.languages import Language, language_for_path
from codesage.core.models import Category, Finding, Severity, Source

# ---------------------------------------------------------------- 上下文


@dataclass
class JavaRuleContext:
    """规则执行上下文：源码 + 语法树 + Finding 构造。"""

    filename: str
    tree: java_parser.JavaTree

    def text(self, node: Any) -> str:
        return self.tree.text(node) if node is not None else ""

    def finding(
        self,
        *,
        rule_id: str,
        node: Any,
        category: Category,
        severity: Severity,
        title: str,
        description: str = "",
        suggestion: str = "",
        confidence: float = 1.0,
        line_start: int | None = None,
        line_end: int | None = None,
        evidence: str = "",
    ) -> Finding:
        start = line_start if line_start is not None else java_parser.line_of(node)
        end = line_end if line_end is not None else java_parser.end_line_of(node)
        if not evidence:
            evidence = self._line_evidence(start)
        return Finding(
            file=self.filename,
            line_start=start,
            line_end=max(start, end),
            category=category,
            severity=severity,
            title=title,
            description=description,
            suggestion=suggestion,
            evidence=evidence[:200],
            confidence=confidence,
            source=Source.JAVA_RULE,
            rule_id=rule_id,
            language=Language.JAVA.value,
        )

    def _line_evidence(self, line: int) -> str:
        lines = self.tree.lines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
        return ""


# ---------------------------------------------------------------- 基类


class JavaRule(ABC):
    """一条 Java 规则。"""

    id: str = "JG000"
    title: str = ""
    skip_in_tests: bool = False

    @abstractmethod
    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        """执行检测，返回发现列表。"""


def _iter_nodes(tree: java_parser.JavaTree, *types: str) -> Iterator[Any]:
    wanted = set(types)
    for node in java_parser.walk_named(tree.root):
        if node.type in wanted:
            yield node


def _catch_type_text(ctx: JavaRuleContext, catch_clause: Any) -> str:
    """catch 声明的异常类型文本（多捕获会带 `|`）。"""
    for child in catch_clause.named_children:
        if child.type == "catch_formal_parameter":
            for sub in child.named_children:
                if sub.type == "catch_type":
                    return ctx.text(sub)
    return ""


def _arguments(node: Any) -> list[Any]:
    """方法调用的实参节点列表。"""
    args = node.child_by_field_name("arguments")
    if args is None:
        return []
    return list(args.named_children)


def _string_content(ctx: JavaRuleContext, node: Any) -> str | None:
    """字符串字面量的内容（去掉引号/文本块三引号）；非字面量返回 None。"""
    if node is None or node.type != "string_literal":
        return None
    raw = ctx.text(node).strip()
    if raw.startswith('"""'):
        return raw[3:-3].strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return raw


def _is_literal(node: Any) -> bool:
    """是否为"编译期常量"级别的实参（字面量或常量拼接）。"""
    if node is None:
        return False
    if node.type in {"string_literal", "decimal_integer_literal", "true", "false"}:
        return True
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and operator.type == "+":
            return _is_literal(node.child_by_field_name("left")) and _is_literal(
                node.child_by_field_name("right")
            )
    return False


def _ancestor_types(node: Any, stop: str = "method_declaration") -> Iterator[str]:
    """向上收集祖先节点类型（到方法边界为止）。"""
    current = node.parent
    while current is not None:
        yield current.type
        if current.type == stop:
            return
        current = current.parent


# ---------------------------------------------------------------- JG001


class EmptyCatchRule(JavaRule):
    """空 catch（含"只有注释"的 catch）——异常被静默吞没。

    这是跨语言通用的问题：Python 侧对应 CG003（except: pass）。
    Java 里更危险，因为它常出现在"我知道会抛但懒得处理"的位置，
    一旦真实故障发生，线上只会表现为"什么都没发生"。
    """

    id = "JG001"
    title = "空 catch 块"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for clause in _iter_nodes(ctx.tree, "catch_clause"):
            body = clause.child_by_field_name("body")
            if body is None:
                continue
            meaningful = [
                child
                for child in body.named_children
                if child.type not in {"line_comment", "block_comment"}
            ]
            if meaningful:
                continue
            exception_type = _catch_type_text(ctx, clause) or "异常"
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=clause,
                    category=Category.BUG,
                    severity=Severity.MAJOR,
                    title=f"空 catch 块：{exception_type} 被静默吞没",
                    description=(
                        "catch 块内没有任何处理逻辑（或只有注释），异常被完全丢弃。"
                        "调用方无法感知失败，问题会在更远的地方以更难排查的形式暴露。"
                    ),
                    suggestion=(
                        "至少记录日志并保留原始异常（log.error(\"...\", e)）；"
                        "若确实需要忽略，请写明理由并缩小捕获范围。"
                    ),
                    confidence=1.0,
                )
            )
        return findings


# ---------------------------------------------------------------- JG002


_BROAD_EXCEPTIONS = {
    "Throwable": (Category.BUG, Severity.MAJOR, 0.85),
    "Exception": (Category.BEST_PRACTICE, Severity.MINOR, 0.7),
    "RuntimeException": (Category.BEST_PRACTICE, Severity.MINOR, 0.6),
}


class BroadCatchRule(JavaRule):
    """过宽异常捕获：catch Throwable / Exception / RuntimeException。

    对应 Python 侧的 CG010（except Exception）。宽捕获会把"不该被忽略的
    编程错误"（NPE、IllegalState）和"可恢复的业务异常"混在一起处理。
    """

    id = "JG002"
    title = "过宽异常捕获"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for clause in _iter_nodes(ctx.tree, "catch_clause"):
            type_text = _catch_type_text(ctx, clause)
            if not type_text:
                continue
            caught = [part.strip().split("<")[0] for part in type_text.split("|")]
            hit = next((name for name in caught if name in _BROAD_EXCEPTIONS), None)
            if hit is None:
                continue
            category, severity, confidence = _BROAD_EXCEPTIONS[hit]
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=clause,
                    category=category,
                    severity=severity,
                    title=f"过宽异常捕获：catch ({hit})",
                    description=(
                        f"捕获 {hit} 会把可恢复的业务异常与编程错误（空指针、状态错误、"
                        "断言失败）混为一谈，容易掩盖真实缺陷。"
                    ),
                    suggestion=(
                        "按具体异常类型分别处理（IOException / SQLException / "
                        "业务自定义异常）；确需兜底时只在最外层使用并完整记录上下文。"
                    ),
                    confidence=confidence,
                )
            )
        return findings


# ---------------------------------------------------------------- JG003


class CommandExecutionRule(JavaRule):
    """命令执行入口：Runtime.exec / ProcessBuilder。

    对应 Python 侧的 CG013（os.system / subprocess）。这是注入面里危害最大的
    一类——一旦参数来自请求，等于把 shell 交给外部输入。
    """

    id = "JG003"
    title = "命令执行入口"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for call in _iter_nodes(ctx.tree, "method_invocation"):
            name_node = call.child_by_field_name("name")
            if name_node is None or ctx.text(name_node) != "exec":
                continue
            receiver = call.child_by_field_name("object")
            receiver_text = re.sub(r"\s+", "", ctx.text(receiver))
            if receiver_text not in {"Runtime.getRuntime()", "runtime", "Runtime"}:
                continue
            findings.append(self._report(ctx, call, "Runtime.exec"))

        for creation in _iter_nodes(ctx.tree, "object_creation_expression"):
            type_node = creation.child_by_field_name("type")
            if type_node is None:
                continue
            type_text = ctx.text(type_node).split("<")[0].strip()
            if not type_text.endswith("ProcessBuilder"):
                continue
            findings.append(self._report(ctx, creation, "ProcessBuilder"))
        return findings

    def _report(
        self, ctx: JavaRuleContext, node: Any, api: str
    ) -> Finding:
        arguments = _arguments(node)
        dynamic = [arg for arg in arguments if not _is_literal(arg)]
        if dynamic:
            return ctx.finding(
                rule_id=self.id,
                node=node,
                category=Category.SECURITY,
                severity=Severity.MAJOR,
                title=f"命令执行入口：{api} 接收非字面量参数",
                description=(
                    f"{api} 的实参包含运行时拼接的变量。若其中任何一段来自用户输入，"
                    "攻击者可以注入额外的命令或参数（命令注入）。"
                ),
                suggestion=(
                    "改用 ProcessBuilder 的数组形式并逐个传参（不拼 shell 字符串）；"
                    "对输入做白名单校验；避免经过 sh -c / cmd /c。"
                ),
                confidence=0.9,
            )
        return ctx.finding(
            rule_id=self.id,
            node=node,
            category=Category.SECURITY,
            severity=Severity.INFO,
            title=f"命令执行调用：{api}（参数全为字面量）",
            description=(
                f"{api} 的实参均为编译期常量，当前不构成注入风险；"
                "但这类 API 一旦后续改为动态参数就会立刻变成漏洞入口。"
            ),
            suggestion="如需保留，建议集中封装并在注释中说明参数来源可信。",
            confidence=0.5,
        )


# ---------------------------------------------------------------- JG004


_CREDENTIAL_NAME = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|apikey|api_key|accesskey|access_key|privatekey|private_key)"
)
_PLACEHOLDER_HINTS = (
    "xxx", "your", "changeme", "change_me", "placeholder", "example",
    "dummy", "todo", "fixme", "***", "<", "sample", "test",
)


class HardcodedCredentialRule(JavaRule):
    """硬编码凭据：字面量直接赋给 password / secret / token 类字段或变量。

    为什么这里用"语法树定位 + 名称正则"而不是纯正则：
    纯正则会命中注释和日志文案；而完整语义检测需要常量折叠与调用图。
    折中方案是：定位到"声明/赋值节点"再查名称与字面量特征。

    精度取舍：要求字面量"看起来像凭据"（含数字/大写/符号），
    纯小写字母的单词（如 `String password = "password"` 这类字段名回填）
    不报——这类多是常量定义或测试占位。
    """

    id = "JG004"
    title = "硬编码凭据"
    skip_in_tests = True

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for declarator in _iter_nodes(ctx.tree, "variable_declarator"):
            name_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            name = ctx.text(name_node)
            if not _CREDENTIAL_NAME.search(name):
                continue
            content = _string_content(ctx, value_node)
            if content is None or not self._looks_like_secret(content):
                continue
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=declarator,
                    category=Category.SECURITY,
                    severity=Severity.MAJOR,
                    title=f"硬编码凭据：{name}",
                    description=(
                        "敏感值以字面量形式写在源码里，会随代码进入版本库、构建产物与镜像层，"
                        "泄露后无法通过改代码以外的手段吊销。"
                    ),
                    suggestion=(
                        "改为从环境变量 / 配置中心 / 密钥管理服务读取；"
                        "若该值已提交过，请立刻轮换。"
                    ),
                    confidence=0.7,
                )
            )
        return findings

    @staticmethod
    def _looks_like_secret(content: str) -> bool:
        if len(content) < 6:
            return False
        lowered = content.lower()
        if any(hint in lowered for hint in _PLACEHOLDER_HINTS):
            return False
        # 至少要有数字 / 大写 / 符号之一，纯小写单词多为名称回填或占位
        return any(
            char.isdigit() or char.isupper() or not char.isalnum() for char in content
        )


# ---------------------------------------------------------------- JG005


_SQL_METHODS = {
    "executeQuery",
    "executeUpdate",
    "execute",
    "executeLargeUpdate",
    "createQuery",
    "createNativeQuery",
    "prepareStatement",
}
_SQL_STATEMENT = re.compile(
    r"(?is)\b(select|insert|update|delete)\b[\s\S]*\b(from|where|into|set|values)\b"
)


class SqlConcatenationRule(JavaRule):
    """SQL 字符串拼接：把变量拼进 SQL 文本再执行。

    对应 Python 侧的 f-string 拼 SQL。检测两条路径：
    1. 直接拼接在执行方法的实参里；
    2. 先赋给局部变量再作为标识符传入 —— 通过"同方法内回查声明"补上，
       否则最常见的写法（String sql = ... + id; stmt.execute(sql);）会漏掉。

    仅做单方法内的回查：跨方法追踪需要符号表，属于阶段二。
    """

    id = "JG005"
    title = "SQL 字符串拼接"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for call in _iter_nodes(ctx.tree, "method_invocation"):
            name_node = call.child_by_field_name("name")
            if name_node is None or ctx.text(name_node) not in _SQL_METHODS:
                continue
            arguments = _arguments(call)
            if not arguments:
                continue
            first = arguments[0]

            if self._is_sql_concatenation(ctx, first):
                findings.append(self._report(ctx, call, ctx.text(name_node)))
                continue

            if first.type == "identifier":
                declaration = self._find_declaration(ctx, call, ctx.text(first))
                if declaration is not None and self._is_sql_concatenation(
                    ctx, declaration.child_by_field_name("value")
                ):
                    findings.append(
                        self._report(ctx, call, ctx.text(name_node), variable=ctx.text(first))
                    )
        return findings

    @staticmethod
    def _is_sql_concatenation(ctx: JavaRuleContext, node: Any) -> bool:
        if node is None or node.type != "binary_expression":
            return False
        operator = node.child_by_field_name("operator")
        if operator is None or operator.type != "+":
            return False
        text = ctx.text(node)
        if not _SQL_STATEMENT.search(text):
            return False
        # 必须有非字面量参与，纯常量拼接不算（编译期已成常量）
        return not _is_literal(node)

    @staticmethod
    def _find_declaration(ctx: JavaRuleContext, call: Any, name: str) -> Any | None:
        method = java_parser.enclosing_method(call)
        if method is None:
            return None
        for declarator in java_parser.walk_named(method):
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is not None and ctx.text(name_node) == name:
                return declarator
        return None

    def _report(
        self, ctx: JavaRuleContext, node: Any, method: str, variable: str = ""
    ) -> Finding:
        where = f"（变量 {variable}）" if variable else ""
        return ctx.finding(
            rule_id=self.id,
            node=node,
            category=Category.SECURITY,
            severity=Severity.MAJOR,
            title=f"SQL 注入风险：{method} 的语句由字符串拼接{where}",
            description=(
                "SQL 文本由变量拼接而成，变量内容会被数据库当作语句的一部分解析，"
                "攻击者可构造输入改变查询语义（SQL 注入）。"
            ),
            suggestion=(
                "改用 PreparedStatement + `?` 占位符并用 setXxx 绑定参数；"
                "动态表名/排序字段请走白名单枚举，不要拼接。"
            ),
            confidence=0.85,
        )


# ---------------------------------------------------------------- JG006


_RESOURCE_TYPES = frozenset(
    {
        "FileInputStream", "FileOutputStream", "FileReader", "FileWriter",
        "BufferedReader", "BufferedWriter", "PrintWriter",
        "Connection", "Statement", "PreparedStatement", "ResultSet",
        "Socket", "ServerSocket", "Scanner",
    }
)


class UnclosedResourceRule(JavaRule):
    """资源未用 try-with-resources：句柄在 try 外创建且未显式关闭。

    对应 Python 侧的 CG005（open 未用 with）。Java 的正确答案是
    try-with-resources，它比手动 finally close 更不容易漏（异常路径也覆盖）。
    """

    id = "JG006"
    title = "资源未用 try-with-resources"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for declaration in _iter_nodes(ctx.tree, "local_variable_declaration"):
            type_text = java_parser.declared_type(ctx.tree, declaration).split("<")[0].strip()
            if type_text not in _RESOURCE_TYPES:
                continue
            declarator = declaration.child_by_field_name("declarator")
            value = declarator.child_by_field_name("value") if declarator else None
            if value is None or value.type != "object_creation_expression":
                continue
            # 已在 try-with-resources 的 resource 里 → 正确写法
            if any(
                ancestor in {"resource_specification", "resource"}
                for ancestor in _ancestor_types(declaration)
            ):
                continue
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=declaration,
                    category=Category.BUG,
                    severity=Severity.MINOR,
                    title=f"资源未用 try-with-resources：{type_text}",
                    description=(
                        f"`{type_text}` 在 try 之外创建，方法中途抛异常时 close() 不会执行，"
                        "会累积文件句柄 / 连接 / 游标泄漏。"
                    ),
                    suggestion=(
                        f"改为 `try ({type_text} res = new {type_text}(...)) {{ ... }}`；"
                        "关闭顺序与打开顺序相反，且不要吞掉 close 的异常。"
                    ),
                    confidence=0.6,
                )
            )
        return findings


# ---------------------------------------------------------------- JG007


class StringConcatInLoopRule(JavaRule):
    """循环内字符串 `+=`：O(n²) 的隐式拷贝。

    对应 Python 侧的 CG007。Java 里更隐蔽：`s += x` 等价于
    `s = new StringBuilder().append(s).append(x).toString()`，
    每轮都复制已有全部内容。
    """

    id = "JG007"
    title = "循环内字符串拼接"
    skip_in_tests = True

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for assignment in _iter_nodes(ctx.tree, "assignment_expression"):
            operator = assignment.child_by_field_name("operator")
            if operator is None or operator.type != "+=":
                continue
            value = assignment.child_by_field_name("right")
            if value is None:
                continue
            # 右侧是字符串字面量/含字面量的拼接，或左侧是 String 变量（`s += row`）。
            # 后一种必须靠"回查同方法内的声明类型"才能判断——只认字面量会漏掉
            # 最常见的写法，这与 Python 侧 CG007 的类型启发式是同一取舍。
            if not (self._has_string(ctx, value) or self._left_is_string(ctx, assignment)):
                continue
            if not any(
                ancestor
                in {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
                for ancestor in _ancestor_types(assignment)
            ):
                continue
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=assignment,
                    category=Category.PERFORMANCE,
                    severity=Severity.MINOR,
                    title="循环内使用字符串 `+=` 拼接",
                    description=(
                        "每次 `+=` 都会创建新的 String 并复制已有内容，"
                        "循环 m 次、字符串长度 n 时复杂度为 O(m²n)。"
                    ),
                    suggestion="循环外用 StringBuilder 累加，结束后 toString()。",
                    confidence=0.7,
                )
            )
        return findings

    @staticmethod
    def _has_string(ctx: JavaRuleContext, node: Any) -> bool:
        if node.type == "string_literal":
            return True
        return any(
            child.type == "string_literal" for child in java_parser.walk_named(node)
        )

    @staticmethod
    def _left_is_string(ctx: JavaRuleContext, assignment: Any) -> bool:
        """左侧标识符是否是同方法内声明的 String 局部变量。"""
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return False
        name = ctx.text(left)
        method = java_parser.enclosing_method(assignment)
        if method is None:
            return False
        for declaration in java_parser.walk_named(method):
            if declaration.type != "local_variable_declaration":
                continue
            declared = java_parser.declared_type(ctx.tree, declaration).split("<")[0].strip()
            if declared != "String":
                continue
            for declarator in declaration.named_children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                if name_node is not None and ctx.text(name_node) == name:
                    return True
        return False


# ---------------------------------------------------------------- JG008


_NON_THREAD_SAFE_FORMAT = frozenset({"SimpleDateFormat", "DateFormat"})


class StaticSimpleDateFormatRule(JavaRule):
    """static 的 SimpleDateFormat：共享实例不是线程安全的。

    Java 生态的经典陷阱。`SimpleDateFormat` 内部用可变 Calendar 保存中间状态，
    并发调用会产出错乱的日期甚至抛 NumberFormatException。
    这是"语言领域知识"型检查，通用 linter 不会报。
    """

    id = "JG008"
    title = "静态 SimpleDateFormat"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for field in _iter_nodes(ctx.tree, "field_declaration"):
            if not self._is_static(ctx, field):
                continue
            type_text = java_parser.declared_type(ctx.tree, field).split("<")[0].strip()
            if type_text not in _NON_THREAD_SAFE_FORMAT:
                continue
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=field,
                    category=Category.BUG,
                    severity=Severity.MAJOR,
                    title=f"static 的 {type_text}：非线程安全却被共享",
                    description=(
                        f"{type_text} 内部持有可变的日历状态，被多线程同时 format/parse 时"
                        "会互相污染，产生错误结果或运行时异常。"
                    ),
                    suggestion=(
                        "改用不可变的 DateTimeFormatter（java.time）；"
                        "或每次使用时新建实例，或用 ThreadLocal 隔离。"
                    ),
                    confidence=0.9,
                )
            )
        return findings

    @staticmethod
    def _is_static(ctx: JavaRuleContext, field: Any) -> bool:
        for child in field.children:
            if child.type == "modifiers":
                return "static" in ctx.text(child).split()
        return False


# ---------------------------------------------------------------- JG009


class EqualsWithoutHashCodeRule(JavaRule):
    """重写 equals 却没重写 hashCode：违反 equals/hashCode 契约。

    后果是对象放进 HashMap/HashSet 后"取不出来"——因为散列桶按 hashCode 定位，
    而默认 hashCode 是身份哈希。这也是 Effective Java 的第一批条款之一。
    """

    id = "JG009"
    title = "equals 未配套 hashCode"

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for declaration in _iter_nodes(ctx.tree, *sorted(java_parser.TYPE_DECLARATION_TYPES)):
            body = declaration.child_by_field_name("body")
            if body is None:
                continue
            equals_method = None
            has_hash_code = False
            for method in java_parser.iter_methods(body):
                name = java_parser.method_name(ctx.tree, method)
                if name == "hashCode":
                    has_hash_code = True
                elif name == "equals" and self._takes_single_object(ctx, method):
                    equals_method = method
            if equals_method is None or has_hash_code:
                continue
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=equals_method,
                    category=Category.BUG,
                    severity=Severity.MINOR,
                    title="重写了 equals 但未重写 hashCode",
                    description=(
                        "equals 判定相等的两个对象，若 hashCode 不同，"
                        "在 HashMap / HashSet / ConcurrentHashMap 中会被分到不同桶，"
                        "表现为「放进去却查不到」。"
                    ),
                    suggestion=(
                        "同时重写 hashCode 并保持同一组字段；"
                        "优先用 IDE 生成或 `Objects.hash(...)` / record。"
                    ),
                    confidence=0.9,
                )
            )
        return findings

    @staticmethod
    def _takes_single_object(ctx: JavaRuleContext, method: Any) -> bool:
        parameters = method.child_by_field_name("parameters")
        if parameters is None:
            return False
        formal = [
            child for child in parameters.named_children if child.type == "formal_parameter"
        ]
        if len(formal) != 1:
            return False
        type_text = java_parser.declared_type(ctx.tree, formal[0]).strip()
        return type_text.endswith("Object")


# ---------------------------------------------------------------- JGCC


class ComplexityRule(JavaRule):
    """圈复杂度（Java 侧没有 radon，用 tree-sitter 自算）。

    复用与 Python 完全相同的阈值（10 / 20），这样两种语言的报告可以用
    同一句话解释："CC ≥ 10 需要关注，≥ 20 需要重构"。
    """

    id = "JGCC"
    title = "圈复杂度"
    skip_in_tests = True

    def check(self, ctx: JavaRuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for method in _iter_nodes(ctx.tree, "method_declaration", "constructor_declaration"):
            complexity = java_parser.cyclomatic_complexity(ctx.tree, method)
            if complexity < COMPLEXITY_MINOR:
                continue
            severity = (
                Severity.MAJOR if complexity >= COMPLEXITY_MAJOR else Severity.MINOR
            )
            label = java_parser.qualified_method_name(ctx.tree, method)
            findings.append(
                ctx.finding(
                    rule_id=self.id,
                    node=method,
                    category=Category.PERFORMANCE,
                    severity=severity,
                    title=f"圈复杂度过高：{label}（CC={complexity}）",
                    description=(
                        f"方法 {label} 的圈复杂度为 {complexity}，超过阈值 "
                        f"{COMPLEXITY_MINOR}。分支越多，路径覆盖与回归成本越高。"
                    ),
                    suggestion=(
                        "提取私有方法消解长分支；用策略/状态模式替换类型分支；"
                        "把复合条件抽成语义命名的小函数。"
                    ),
                    confidence=1.0,
                )
            )
        return findings


# ---------------------------------------------------------------- 引擎


def default_java_rules() -> list[JavaRule]:
    """默认规则集（顺序决定同文件内的报告顺序）。"""
    return [
        EmptyCatchRule(),
        BroadCatchRule(),
        CommandExecutionRule(),
        HardcodedCredentialRule(),
        SqlConcatenationRule(),
        UnclosedResourceRule(),
        StringConcatInLoopRule(),
        StaticSimpleDateFormatRule(),
        EqualsWithoutHashCodeRule(),
        ComplexityRule(),
    ]


JAVA_RULE_IDS = [rule.id for rule in default_java_rules()]


class JavaRuleEngine:
    """Java 规则引擎（与 Python 的 RuleEngine 同构，但语言互斥）。"""

    name = "java_rule"
    display = "Java 规则（自研）"
    language = Language.JAVA
    languages: tuple[Language, ...] = (Language.JAVA,)
    unavailable_hint = (
        "未安装 tree-sitter-java：Java 的确定性规则与复杂度分析已跳过"
        '（LLM 语义审查不受影响）。安装：pip install -e ".[java]"'
    )

    def __init__(self, rules: list[JavaRule] | None = None) -> None:
        self.rules = rules if rules is not None else default_java_rules()

    def supports(self, language: Language) -> bool:
        return language in self.languages

    def supports_any(self, languages: object) -> bool:
        return any(self.supports(lang) for lang in languages)  # type: ignore[union-attr]

    def is_available(self) -> bool:
        return java_parser.is_available()

    def run(self, filename: str, source: str) -> list[Finding]:
        """执行全部规则。

        语言守卫是双保险：pipeline 已经按语言分组，这里再挡一次，
        防止未来某个调用点直接把 .py 喂进来（Python 源码在 Java 语法下
        常常"能解析但语义全错"，产出的是纯噪音）。
        """
        detected = language_for_path(filename)
        if detected is not None and detected is not Language.JAVA:
            return []
        if not self.is_available():
            return []

        tree = java_parser.parse_java(source)
        if tree is None:
            return []

        ctx = JavaRuleContext(filename=filename, tree=tree)
        is_test = is_test_file(filename, Language.JAVA)
        findings: list[Finding] = []
        for rule in self.rules:
            if is_test and rule.skip_in_tests:
                continue
            try:
                findings.extend(rule.check(ctx))
            except Exception:  # noqa: BLE001 - 单条规则失败不拖垮整体
                continue
        return findings
