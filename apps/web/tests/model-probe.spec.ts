import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { login } from "../src/api/auth";
import { createAppRouter } from "../src/router";
import { server } from "../src/mocks/server";
import SettingsView from "../src/views/SettingsView.vue";

async function mountSettings() {
  await login({ email: "demo@example.test", password: "password" });
  const router = createAppRouter({ isAuthenticated: true, restore: async () => {} });
  await router.push("/settings");
  const wrapper = mount(SettingsView, { global: { plugins: [createPinia(), router] } });
  await flushPromises();
  return wrapper;
}

it("测试连接成功后显示延迟与结论", async () => {
  const wrapper = await mountSettings();
  const chat = wrapper.findAll("article")[0]!;
  const button = chat.findAll("button").find((item) => item.text().includes("测试连接"))!;
  await button.trigger("click");
  await flushPromises();
  const result = chat.get('[role="status"]');
  expect(result.text()).toContain("✓ 连接正常（123 ms）");
  expect(result.text()).toContain("chat 模型响应正常");
  wrapper.unmount();
});

it("测试连接失败时按告警样式展示供应商原因", async () => {
  server.use(
    http.post("*/settings/models/:kind/test", () =>
      HttpResponse.json({ ok: false, latencyMs: 88, detail: "供应商返回 401 Unauthorized" }),
    ),
  );
  const wrapper = await mountSettings();
  const chat = wrapper.findAll("article")[0]!;
  const button = chat.findAll("button").find((item) => item.text().includes("测试连接"))!;
  await button.trigger("click");
  await flushPromises();
  const result = chat.get('[role="alert"]');
  expect(result.text()).toContain("✗ 供应商返回 401 Unauthorized");
  wrapper.unmount();
});
