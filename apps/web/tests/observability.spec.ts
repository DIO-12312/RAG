import { flushPromises, mount } from "@vue/test-utils";
import { http, HttpResponse } from "msw";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "../src/router";
import { useAuthStore } from "../src/stores/auth";
import { server } from "../src/mocks/server";
import ObservabilityView from "../src/views/ObservabilityView.vue";

describe("administrator observability", () => {
  it("rejects direct navigation when current role was revoked", async () => {
    const auth = { isAuthenticated: true, isAdmin: false, restore: async () => {}, refresh: async () => {} };
    const router = createAppRouter(auth);
    await router.push("/admin/observability");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("overview");
    auth.isAdmin = true;
    await router.push("/admin/observability");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("observability");
    auth.isAdmin = false;
    await router.push("/settings");
    await router.push("/admin/observability");
    expect(router.currentRoute.value.name).toBe("overview");
  });

  it("renders empty and unavailable states without exposing raw span attributes", async () => {
    server.use(
      http.get("*/admin/observability/metrics", () => HttpResponse.json({ status: "empty", window: "1h", panels: [] })),
      http.get("*/admin/observability/traces", () => HttpResponse.json({ status: "empty", service: "rag-go-api", window: "1h", traces: [] })),
    );
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "admin", email: "admin@example.test", language: "zh-CN", role: "admin" };
    const router = createAppRouter({ isAuthenticated: true, isAdmin: true, restore: async () => {}, refresh: async () => {} });
    await router.push("/admin/observability");
    await router.isReady();
    const wrapper = mount(ObservabilityView, { global: { plugins: [pinia, router] } });
    await flushPromises();
    expect(wrapper.text()).toContain("暂无指标");
    expect(wrapper.text()).toContain("暂无链路");
    server.use(http.get("*/admin/observability/metrics", () => HttpResponse.json({ code: "OBSERVABILITY_UNAVAILABLE" }, { status: 503 })));
    await wrapper.get(".obs-header button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("指标服务暂不可用");
    expect(wrapper.text()).not.toContain("api_key");
    wrapper.unmount();
  });
});
