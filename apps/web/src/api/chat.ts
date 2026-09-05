import type { ChatEvent, ChatRequest } from "./contracts";
import { createMockChatStream } from "@/mocks/sse";

export interface ChatStream {
  events: AsyncIterable<ChatEvent>;
  cancel(): void;
}

export function streamChat(_request: ChatRequest): ChatStream {
  void _request;
  return createMockChatStream();
}
