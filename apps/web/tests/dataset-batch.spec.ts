import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { login } from "../src/api/auth";
import { createAppRouter } from "../src/router";
import { server } from "../src/mocks/server";
import DatasetDetailView from "../src/views/DatasetDetailView.vue";

const dataset = {
  id: "dataset-ready",
  name: "产品发布资料",
  status: "READY",
  documentCount: 3,
  updatedAt: "2026-09-05T09:30:00Z",
  documents: [
    { id: "doc-a", name: "a.pdf", status: "INDEXED", jobId: "job-a" },
    { id: "doc-b", name: "b.pdf", status: "INDEXED", jobId: "job-b" },
    { id: "doc-c", name: "c.pdf", status: "FAILED", jobId: "job-c" },
  ],
};

async function mountDetail(counters: { deleted: string[]; retried: string[] }) {
  server.use(
    http.get("*/datasets/:id", () => HttpResponse.json(dataset)),
    http.delete("*/documents/:id", ({ params }) => {
      counters.deleted.push(String(params.id));
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("*/jobs/:id/retry", ({ params }) => {
      counters.retried.push(String(params.id));
      return HttpResponse.json({ id: String(params.id), datasetId: dataset.id, sourceName: "c.pdf", status: "RUNNING", progress: 0, retryable: false });
    }),
  );
  await login({ email: "demo@example.test", password: "password" });
  const router = createAppRouter({ isAuthenticated: true, restore: async () => {} });
  await router.push("/datasets/dataset-ready");
  await router.isReady();
  const wrapper = mount(DatasetDetailView, {
    global: { plugins: [createPinia(), router], stubs: { UploadPanel: true, JobTable: true } },
  });
  await flushPromises();
  return wrapper;
}

it("批量删除只处理选中的文档并汇总结果", async () => {
  const counters = { deleted: [] as string[], retried: [] as string[] };
  const wrapper = await mountDetail(counters);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const toolbar = wrapper.get(".document-toolbar");
  expect(wrapper.findAll('article input[type="checkbox"]')).toHaveLength(3);
  expect(toolbar.text()).toContain("已选 0 / 3");

  await toolbar.get('input[type="checkbox"]').setValue(true);
  await wrapper.findAll('article input[type="checkbox"]')[2]!.setValue(false);
  expect(wrapper.get(".document-toolbar").text()).toContain("已选 2 / 3");

  const buttons = wrapper.get(".document-toolbar").findAll("button");
  await buttons[0]!.trigger("click");
  await flushPromises();
  expect(counters.deleted).toEqual(["doc-a", "doc-b"]);
  expect(wrapper.get('[role="status"]').text()).toContain("已删除 2 个文档。");
  wrapper.unmount();
});

it("重新索引失败项只提交 FAILED 文档的任务", async () => {
  const counters = { deleted: [] as string[], retried: [] as string[] };
  const wrapper = await mountDetail(counters);
  const toolbar = wrapper.get(".document-toolbar");
  const buttons = toolbar.findAll("button");
  expect(buttons[1]!.attributes("disabled")).toBeDefined();

  await wrapper.findAll('article input[type="checkbox"]')[2]!.setValue(true);
  const retryButton = wrapper.get(".document-toolbar").findAll("button")[1]!;
  expect(retryButton.text()).toContain("重新索引失败项（1）");
  await retryButton.trigger("click");
  await flushPromises();
  expect(counters.retried).toEqual(["job-c"]);
  expect(wrapper.get('[role="status"]').text()).toContain("已重新提交 1 个文档的索引任务。");
  wrapper.unmount();
});
