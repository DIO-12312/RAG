import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { login } from "../src/api/auth";
import { createAppRouter } from "../src/router";
import { server } from "../src/mocks/server";
import ChatView from "../src/views/ChatView.vue";

function sse(context: string): HttpResponse<string> {
  return new HttpResponse(
    `${context}event: final\ndata: {"answer":"已按现有资料回答。","citations":[],"conversationId":"c"}\n\n`,
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

async function askOnce(context: string) {
  server.use(http.post("*/chat/stream", () => sse(context)));
  await login({ email: "demo@example.test", password: "password" });
  const router = createAppRouter({ isAuthenticated: true, restore: async () => {} });
  await router.push("/");
  const wrapper = mount(ChatView, { global: { plugins: [createPinia(), router] } });
  await flushPromises();
  await flushPromises();
  await wrapper.get("textarea").setValue("迁移窗口是什么时候？");
  await wrapper.get("form.chat-composer").trigger("submit");
  await flushPromises();
  await flushPromises();
  return wrapper;
}

it("上下文接近预算时显示占用告警", async () => {
  const wrapper = await askOnce(
    'event: context\ndata: {"estimatedTokens":27648,"usableTokens":28672,"budgetTokens":32768,"evidenceCount":23,"evidenceLimit":40}\n\n',
  );
  const warning = wrapper.get(".context-warning");
  expect(warning.text()).toContain("上下文已用 96%");
  expect(warning.text()).toContain("27,648 / 28,672 tokens");
  expect(warning.text()).toContain("可能被裁剪");
  wrapper.unmount();
});

it("证据达到上限时单独提示，且低占用不显示告警", async () => {
  const capped = await askOnce(
    'event: context\ndata: {"estimatedTokens":4096,"usableTokens":28672,"budgetTokens":32768,"evidenceCount":40,"evidenceLimit":40}\n\n',
  );
  expect(capped.get(".context-warning").text()).toContain("证据条数已达上限（40 / 40）");
  capped.unmount();

  const low = await askOnce(
    'event: context\ndata: {"estimatedTokens":2048,"usableTokens":28672,"budgetTokens":32768,"evidenceCount":4,"evidenceLimit":40}\n\n',
  );
  expect(low.find(".context-warning").exists()).toBe(false);
  low.unmount();
});
