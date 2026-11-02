import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
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
  placeholder?: string;
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
  placeholder = "从左侧选择一个文件",
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);

  // 创建 / 销毁编辑器
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: "",
        extensions: [
          lineNumbers(),
          python(),
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
  }, []);

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
