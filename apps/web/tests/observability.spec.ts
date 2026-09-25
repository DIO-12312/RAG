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

  it("shows scrape health and fixed RAG stage series", async () => {
    server.use(
      http.get("*/admin/observability/metrics", () => HttpResponse.json({ status: "ok", window: "1h", panels: [
        { key: "telemetry_targets", status: "ok", series: [
          { name: "otel-collector", points: [{ time: 1, value: 1 }] },
          { name: "tempo", points: [{ time: 1, value: 0 }] },
        ] },
        { key: "ingestion_stage_p95", status: "ok", series: [{ name: "embedding", points: [{ time: 1, value: 2.5 }] }] },
      ] })),
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
    expect(wrapper.text()).toContain("Collector 指标出口");
    expect(wrapper.text()).toContain("抓取失败");
    expect(wrapper.text()).toContain("暂无抓取状态");
    expect(wrapper.text()).toContain("embedding");
    expect(wrapper.text()).toContain("2.5");
    expect(wrapper.text()).toContain("链路服务");
    wrapper.unmount();
  });

  it("opens trace details in a centered modal and restores focus when closed", async () => {
    const first = "0123456789abcdef0123456789abcdef";
    const second = "fedcba9876543210fedcba9876543210";
    server.use(
      http.get("*/admin/observability/metrics", () => HttpResponse.json({ status: "empty", window: "1h", panels: [] })),
      http.get("*/admin/observability/traces", () => HttpResponse.json({ status: "ok", service: "rag-go-api", window: "1h", traces: [
        { traceId: first, service: "rag-go-api", startedAt: "2026-09-25T00:00:00Z", durationMs: 120 },
        { traceId: second, service: "rag-go-api", startedAt: "2026-09-25T00:01:00Z", durationMs: 80 },
      ] })),
      http.get("*/admin/observability/traces/:traceId", ({ params }) => HttpResponse.json({
        status: "ok", traceId: params.traceId, truncated: false,
        spans: [{ spanId: String(params.traceId).slice(0, 16), service: "rag-go-api", stage: params.traceId === first ? "route" : "finalize", startedAt: "2026-09-25T00:00:00Z", durationMs: 12, outcome: "succeeded" }],
      })),
    );
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.user = { id: "admin", email: "admin@example.test", language: "zh-CN", role: "admin" };
    const router = createAppRouter({ isAuthenticated: true, isAdmin: true, restore: async () => {}, refresh: async () => {} });
    await router.push("/admin/observability");
    await router.isReady();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const wrapper = mount(ObservabilityView, { attachTo: container, global: { plugins: [pinia, router] } });
    await flushPromises();

    const buttons = wrapper.findAll(".obs-table-wrap > table > tbody > tr button");
    expect(buttons).toHaveLength(2);
    await buttons[0].trigger("click");
    await flushPromises();
    const modal = document.body.querySelector<HTMLElement>(".obs-trace-card");
    expect(modal?.getAttribute("role")).toBe("dialog");
    expect(modal?.getAttribute("aria-modal")).toBe("true");
    expect(modal?.textContent).toContain(first);
    expect(modal?.textContent).toContain("route");
    expect(modal?.querySelectorAll(".obs-waterfall-row")).toHaveLength(1);
    expect(wrapper.find(".obs-trace-card").exists()).toBe(false);
    expect(document.body.style.overflow).toBe("hidden");
    const close = document.body.querySelector<HTMLButtonElement>('button[aria-label="关闭链路详情"]');
    expect(document.activeElement).toBe(close);
    close?.click();
    await flushPromises();
    expect(document.body.querySelector(".obs-trace-overlay")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(document.activeElement).toBe(buttons[0].element);

    await buttons[1].trigger("click");
    await flushPromises();
    const secondModal = document.body.querySelector<HTMLElement>(".obs-trace-card");
    expect(secondModal?.textContent).toContain(second);
    expect(secondModal?.textContent).toContain("finalize");
    secondModal?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(document.body.querySelector(".obs-trace-overlay")).toBeNull();
    wrapper.unmount();
    container.remove();
  });
});
