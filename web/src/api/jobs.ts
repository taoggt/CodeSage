// 服务端所有事件名（与 server/jobs.py 的 publish 保持一致）。
// EventSource 只能通过 addEventListener(name) 订阅具名事件，所以这里显式列举。
export const JOB_EVENT_TYPES = [
  "queued",
  "started",
  "phase",
  "progress",
  "scan",
  "changed_fallback",
  "static_done",
  "start",
  "context",
  "tool_calls",
  "tool_result",
  "assistant_message",
  "compress",
  "finish",
  "cancelled",
  "cancel_requested",
  "persist_failed",
  "eval_start",
  "eval_case",
  "end",
  "error",
] as const;

export type JobEventType = (typeof JOB_EVENT_TYPES)[number];

export interface JobStreamHandlers {
  onEvent: (type: string, data: any) => void;
  onError?: () => void;
}

/**
 * 订阅任务事件流。
 * 返回取消函数：组件卸载或终态时必须调用，否则 EventSource 会自动重连。
 */
export function connectJob(jobId: string, handlers: JobStreamHandlers): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  const bound: [string, EventListener][] = [];

  for (const type of JOB_EVENT_TYPES) {
    const listener = (event: Event) => {
      const message = event as MessageEvent<string>;
      let data: any = {};
      try {
        data = JSON.parse(message.data);
      } catch {
        data = { raw: message.data };
      }
      handlers.onEvent(type, data);
    };
    source.addEventListener(type, listener);
    bound.push([type, listener]);
  }

  source.onerror = () => {
    handlers.onError?.();
  };

  return () => {
    for (const [type, listener] of bound) {
      source.removeEventListener(type, listener);
    }
    source.close();
  };
}
