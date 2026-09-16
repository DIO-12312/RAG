import type { ChatEvent, ChatRequest } from "./contracts";
import { checkedFetch } from "./http";
export interface ChatStream { events: AsyncIterable<ChatEvent>; cancel(): void; }
export function streamChat(payload: ChatRequest): ChatStream {
  const abort = new AbortController();
  // Node/jsdom 等多 realm 环境可能提供与 fetch 的 Request 不同源的 AbortSignal；
  // 这类 signal 会让请求在发出前抛 TypeError。浏览器中保持正常取消能力，只有
  // 构造器明确拒绝时才省略 signal，避免测试/嵌入式运行时连请求都无法建立。
  let signal: AbortSignal | undefined = abort.signal;
  try { new Request(window.location.origin, { signal }); } catch { signal = undefined; }
  async function* events(): AsyncIterable<ChatEvent> {
    const response = await checkedFetch("/chat/stream", { method: "POST", body: JSON.stringify(payload), signal });
    if (!response.body) throw new Error("服务器未返回对话流");
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ""; let terminal = false;
    try {
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0,boundary); buffer = buffer.slice(boundary+2);
          const event = block.split("\n").find((line) => line.startsWith("event:"))?.slice(6).trim();
          const data = block.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
          if (!event || !data) continue;
          if (!["context","retrieval","token","final","error"].includes(event)) continue;
          terminal ||= event === "final" || event === "error";
          yield { ...JSON.parse(data), type: event } as ChatEvent;
        }
        if (buffer.length > 2*1024*1024) throw new Error("对话流超过大小限制");
        if (done) { if (!terminal) throw new Error("对话连接中断，请重试"); break; }
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
  return { events: events(), cancel: () => abort.abort() };
}
