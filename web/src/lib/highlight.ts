import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

/**
 * 低饱和语法高亮。
 *
 * 与审查标记的配色分工（重要）：
 * - 审查标记只染**背景**（行底色 + 左侧竖条），色系是红 / 橙 / 琥珀 / 蓝；
 * - 语法高亮只染**文字**，因此刻意避开这四个色相，改用紫（关键字）、
 *   青绿（字符串）、棕（数字）、灰（注释/运算符）。
 * 这样同一行既有 P1 橙色底、又有紫色关键字时，两者不会互相干扰。
 */
const palette = {
  keyword: "#7c3aed", // 紫：语言关键字
  definition: "#5b21b6", // 深紫：函数/类定义名
  string: "#0f766e", // 青绿：字符串
  number: "#92400e", // 棕：数字/布尔/None
  comment: "#94a3b8", // 浅灰：注释（斜体）
  operator: "#64748b", // 灰：运算符/标点
  meta: "#475569", // 深灰：装饰器、魔法命令
  property: "#334155",
};

export const codeHighlightStyle = HighlightStyle.define([
  {
    tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword],
    color: palette.keyword,
    fontWeight: "500",
  },
  {
    tag: [t.definition(t.variableName), t.definition(t.className), t.function(t.variableName)],
    color: palette.definition,
    fontWeight: "500",
  },
  { tag: [t.className, t.typeName, t.namespace], color: palette.definition },
  { tag: [t.string, t.special(t.string)], color: palette.string },
  { tag: [t.number, t.bool, t.null, t.atom], color: palette.number },
  { tag: [t.comment, t.lineComment, t.blockComment], color: palette.comment, fontStyle: "italic" },
  { tag: [t.operator, t.punctuation, t.bracket], color: palette.operator },
  { tag: [t.meta, t.annotation, t.processingInstruction], color: palette.meta },
  { tag: [t.propertyName], color: palette.property },
  { tag: [t.self], color: palette.definition, fontStyle: "italic" },
  { tag: [t.invalid], color: "#b91c1c" },
]);

export const codeHighlighting = syntaxHighlighting(codeHighlightStyle, { fallback: true });
