import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { login } from "../src/api/auth";
import { createAppRouter } from "../src/router";
import { server } from "../src/mocks/server";
import ChatView from "../src/views/ChatView.vue";

function sseResponse(): HttpResponse<string> {
  return new HttpResponse(
    'event: final\ndata: {"answer":"迁移窗口截至 2026 年 12 月 31 日。","citations":[],"conversationId":"c"}\n\n',
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

async function mountChat(counter: { calls: number }) {
  server.use(
    http.post("*/chat/stream", () => {
      counter.calls += 1;
      return sseResponse();
    }),
  );
  await login({ email: "demo@example.test", password: "password" });
  const router = createAppRouter({ isAuthenticated: true, restore: async () => {} });
  await router.push("/");
  const wrapper = mount(ChatView, { global: { plugins: [createPinia(), router] } });
  await flushPromises();
  await flushPromises();
  return wrapper;
}

it("发送 Enter 提交问题，Ctrl+Enter 与输入法组合期间只换行", async () => {
  const counter = { calls: 0 };
  const wrapper = await mountChat(counter);
  const textarea = wrapper.get("textarea");

  await textarea.setValue("迁移窗口是什么时候？");
  await textarea.trigger("keydown", { key: "Enter", ctrlKey: true });
  await flushPromises();
  expect(counter.calls).toBe(0);
  expect(wrapper.get("textarea").element.value).toBe("迁移窗口是什么时候？");

  await textarea.trigger("keydown", { key: "Enter", isComposing: true });
  await flushPromises();
  expect(counter.calls).toBe(0);

  await textarea.trigger("keydown", { key: "Enter" });
  await flushPromises();
  await flushPromises();
  expect(counter.calls).toBe(1);
  expect(wrapper.text()).toContain("迁移窗口截至 2026 年 12 月 31 日。");
  expect(wrapper.get("textarea").element.value).toBe("");
  wrapper.unmount();
});
