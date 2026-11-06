import { java } from "@codemirror/lang-java";
import { python } from "@codemirror/lang-python";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import { useEffect, useRef } from "react";

import {
  applyFindings,
  DecorationFinding,
  editorTheme,
  findingField,
  findingGutter,
  lineAtDom,
  scrollToLine,
} from "../lib/decorations";
import { codeHighlighting } from "../lib/highlight";

interface Props {
  value: string;
  findings: DecorationFinding[];
  activeId?: string | null;
  jumpTo?: { line: number; token: number } | null;
  onSelectLine?: (line: number) => void;
  /** 语言代码（python / java / typescript…），决定语法高亮器 */
  language?: string;
  placeholder?: string;
}

/**
 * 按语言选语法高亮扩展。
 *
 * 未识别的语言退回纯文本（不猜）——猜错的高亮比没有高亮更干扰阅读。
 */
function languageExtension(language?: string) {
  switch ((language ?? "").toLowerCase()) {
    case "java":
      return java();
    case "python":
      return python();
    default:
      return [];
  }
}

/**
 * 只读代码视图。
 * 刻意不用 @uiw/react-codemirror / basicSetup：那会打进自动补全、折叠、lint 等
 * 本项目用不到的能力（约 200KB gzip），而审查场景只需要行号 + 语法高亮 + 行装饰。
 */
export function CodeViewer({
  value,
  findings,
  activeId,
  jumpTo,
  onSelectLine,
  language,
  placeholder = "从左侧选择一个文件",
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  // Compartment 是 CodeMirror 的"可替换配置槽"：切换语言时只需 reconfigure，
  // 不必销毁重建 EditorView（重建会丢滚动位置与选区）
  const languageCompartment = useRef(new Compartment()).current;

  // 创建 / 销毁编辑器（language 变化走下面的 reconfigure，不进依赖数组）
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: "",
        extensions: [
          lineNumbers(),
          languageCompartment.of(languageExtension(language)),
          codeHighlighting,
          findingField,
          findingGutter,
          editorTheme,
          EditorView.lineWrapping,
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
        ],
      }),
      parent: hostRef.current,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切换文件语言：热替换高亮器（不销毁 view）
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: languageCompartment.reconfigure(languageExtension(language)),
    });
  }, [language, languageCompartment]);

  // 切换文件：替换文档内容（不销毁 view，保留滚动容器与样式）
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    });
  }, [value]);

  // findings / 选中项变化 → 重建行装饰
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: applyFindings.of({ findings, activeId: activeId ?? null }) });
  }, [findings, activeId, value]);

  // 跳转定位
  useEffect(() => {
    const view = viewRef.current;
    if (!view || !jumpTo) return;
    scrollToLine(view, jumpTo.line);
  }, [jumpTo]);

  if (!value) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-400">
        {placeholder}
      </div>
    );
  }

  return (
    <div
      className="h-full min-h-0 overflow-hidden"
      onMouseDown={(event) => {
        const view = viewRef.current;
        if (!view || !onSelectLine) return;
        const node = (event.target as HTMLElement).closest(".cm-line") as HTMLElement | null;
        if (!node) return;
        const line = lineAtDom(view, node);
        if (line) onSelectLine(line);
      }}
    >
      <div ref={hostRef} className="h-full" />
    </div>
  );
}
