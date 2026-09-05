import type { ChatEvent, Citation, Evidence } from "@/api/contracts";

const evidence: Evidence = {
  chunkId: "chunk-release-notes-4",
  content: "旧字段将在下一主版本移除。迁移窗口截止至 2026 年 12 月 31 日，之后只接受新版字段。",
  sourceName: "release-notes.pdf",
  locator: "第 4 页",
  scores: { fusionScore: 0.91 },
};

export interface MockChatStream {
  events: AsyncIterable<ChatEvent>;
  cancel(): void;
}

export function createMockChatStream(): MockChatStream {
  let cancelled = false;
  const citation: Citation = { ordinal: 1, evidence };

  async function* createEvents(): AsyncIterable<ChatEvent> {
    if (cancelled) return;
    yield { type: "retrieval", hits: [evidence] };
    if (cancelled) return;
    yield { type: "token", text: "主要兼容性变更是旧字段将被移除，" };
    if (cancelled) return;
    yield { type: "token", text: "迁移窗口截至 2026 年 12 月 31 日。" };
    if (cancelled) return;
    yield {
      type: "final",
      answer: "主要兼容性变更是旧字段将被移除，迁移窗口截至 2026 年 12 月 31 日。[1]",
      citations: [citation],
    };
  }

  return {
    events: createEvents(),
    cancel(): void {
      cancelled = true;
    },
  };
}
