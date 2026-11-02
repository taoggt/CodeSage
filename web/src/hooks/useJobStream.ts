import { useEffect } from "react";

import { connectJob } from "../api/jobs";
import { useJobStore } from "../stores/job";

/** 订阅任务事件流；任务终态时主动关闭（否则 EventSource 会自动重连） */
export function useJobStream(jobId: string | null): void {
  const apply = useJobStore((state) => state.apply);

  useEffect(() => {
    if (!jobId) return;
    const stop = connectJob(jobId, {
      onEvent: (type, data) => {
        apply(type, data);
        if (type === "end") stop();
      },
    });
    return stop;
  }, [jobId, apply]);
}
