import { describe, expect, it } from "vitest";

import { createMockChatStream } from "../src/mocks/sse";

describe("mock chat stream", () => {
  it("emits retrieval before tokens and final citations reference complete evidence", async () => {
    const stream = createMockChatStream();
    const events = [];

    for await (const event of stream.events) {
      events.push(event);
    }

    expect(events[0]?.type).toBe("retrieval");
    expect(events.at(-1)?.type).toBe("final");

    const retrieval = events[0];
    const final = events.at(-1);
    if (retrieval?.type !== "retrieval" || final?.type !== "final") {
      throw new Error("expected retrieval and final events");
    }

    expect(final.citations[0]?.evidence.content).toBe(retrieval.hits[0]?.content);
  });
});
