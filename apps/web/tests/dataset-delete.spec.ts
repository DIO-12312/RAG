import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { describe, expect, it } from "vitest";

import { login } from "../src/api/auth";
import { listDatasets } from "../src/api/datasets";
import { createAppRouter } from "../src/router";
import DatasetDetailView from "../src/views/DatasetDetailView.vue";

describe("dataset deletion", () => {
  it("requires confirmation, removes the dataset, and returns to the library", async () => {
    await login({ email: "demo@example.test", password: "password" });
    const pinia = createPinia();
    const router = createAppRouter({ isAuthenticated: true, restore: async () => {} });
    await router.push("/datasets/dataset-ready");
    await router.isReady();
    const wrapper = mount(DatasetDetailView, {
      attachTo: document.body,
      global: {
        plugins: [pinia, router],
        stubs: { UploadPanel: true, JobTable: true, StatusBadge: true },
      },
    });
    await flushPromises();

    await wrapper.get(".button-danger-quiet").trigger("click");
    await flushPromises();
    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]');
    expect(dialog?.textContent).toContain("产品发布资料");
    expect(dialog?.textContent).toContain("3 个文档");
    expect(dialog?.querySelector(".button-quiet")).toBe(document.activeElement);

    dialog?.querySelector<HTMLButtonElement>(".button-danger")?.click();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/datasets");
    expect((await listDatasets()).map((dataset) => dataset.id)).not.toContain("dataset-ready");
    wrapper.unmount();
  });
});
