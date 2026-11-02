import { RangeSet, RangeSetBuilder, StateEffect, StateField } from "@codemirror/state";
import {
  Decoration,
  DecorationSet,
  EditorView,
  GutterMarker,
  gutter,
} from "@codemirror/view";

export interface DecorationFinding {
  id: string;
  line_start: number;
  line_end: number;
  severity: string;
}

export const applyFindings = StateEffect.define<{
  findings: DecorationFinding[];
  activeId?: string | null;
}>();

interface FindingState {
  decos: DecorationSet;
  lines: Map<number, string>;
}

const RANK: Record<string, number> = { critical: 4, major: 3, minor: 2, info: 1 };

function normalizeSeverity(value: string): string {
  return RANK[value] ? value : "info";
}

/**
 * finding 行级高亮 + gutter 标记的唯一数据源。
 * 一次 dispatch 全量重建（findings ≤ 200 条，重建 < 2ms），不做增量维护。
 */
export const findingField = StateField.define<FindingState>({
  create: () => ({ decos: Decoration.none, lines: new Map() }),

  update(value, tr) {
    const effect = tr.effects.find((item) => item.is(applyFindings));
    if (!effect) return value;

    const doc = tr.state.doc;
    const lines = new Map<number, string>();
    const ranges: { from: number; sev: string; active: boolean }[] = [];
    const activeId = effect.value.activeId ?? null;

    for (const finding of effect.value.findings) {
      const severity = normalizeSeverity(finding.severity);
      const start = Math.max(1, Math.min(doc.lines, finding.line_start || 1));
      const end = Math.max(start, Math.min(doc.lines, finding.line_end || start));
      for (let line = start; line <= end && line - start < 60; line += 1) {
        const current = lines.get(line);
        if (!current || RANK[severity] > RANK[current]) lines.set(line, severity);
      }
      ranges.push({ from: doc.line(start).from, sev: severity, active: activeId === finding.id });
    }

    const sorted = ranges.sort((a, b) => a.from - b.from);
    const decorations = sorted.map((item) =>
      Decoration.line({
        class: `cm-find cm-sev-${item.sev}${item.active ? " cm-find-active" : ""}`,
      }).range(item.from),
    );

    return { decos: Decoration.set(decorations, true), lines };
  },

  provide: (field) => EditorView.decorations.from(field, (value) => value.decos),
});

class SeverityMarker extends GutterMarker {
  constructor(readonly severity: string) {
    super();
  }

  eq(other: GutterMarker): boolean {
    return other instanceof SeverityMarker && other.severity === this.severity;
  }

  toDOM(): HTMLElement {
    const dot = document.createElement("span");
    dot.className = `cm-sevdot cm-sevdot-${this.severity}`;
    return dot;
  }
}

export const findingGutter = gutter({
  class: "cm-finding-gutter",
  markers: (view) => {
    const state = view.state.field(findingField, false);
    if (!state || state.lines.size === 0) return RangeSet.empty;
    const builder = new RangeSetBuilder<GutterMarker>();
    const entries = [...state.lines.entries()].sort((a, b) => a[0] - b[0]);
    for (const [line, severity] of entries) {
      if (line > view.state.doc.lines) continue;
      const position = view.state.doc.line(line).from;
      builder.add(position, position, new SeverityMarker(severity));
    }
    return builder.finish();
  },
});

export const editorTheme = EditorView.theme({
  "&": {
    backgroundColor: "#ffffff",
    color: "#1f2937",
    fontSize: "12.5px",
    height: "100%",
  },
  ".cm-scroller": {
    fontFamily: "JetBrains Mono, Cascadia Code, Consolas, monospace",
    lineHeight: "1.65",
    overflow: "auto",
  },
  ".cm-content": { padding: "10px 0" },
  ".cm-gutters": {
    backgroundColor: "#f8fafc",
    color: "#94a3b8",
    border: "none",
    paddingLeft: "4px",
  },
  ".cm-line": { padding: "0 14px 0 8px" },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-activeLineGutter": { backgroundColor: "transparent" },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, ::selection": { backgroundColor: "#dbeafe" },
});

export function scrollToLine(view: EditorView, line: number): void {
  const target = Math.max(1, Math.min(view.state.doc.lines, line));
  const position = view.state.doc.line(target).from;
  view.dispatch({
    selection: { anchor: position },
    effects: EditorView.scrollIntoView(position, { y: "center" }),
  });
}

export function lineAtDom(view: EditorView, node: HTMLElement): number | null {
  try {
    const position = view.posAtDOM(node, 0);
    return view.state.doc.lineAt(position).number;
  } catch {
    return null;
  }
}
