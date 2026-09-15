import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import JobTable from "../src/components/JobTable.vue";

describe("JobTable", () => {
  it("shows retry only for a retryable failed job", () => {
    const wrapper = mount(JobTable, {
      props: {
        jobs: [
          {
            id: "failed",
            datasetId: "dataset-ready",
            sourceName: "failed.pdf",
            status: "FAILED",
            progress: 62,
            retryable: true,
          },
          {
            id: "running",
            datasetId: "dataset-ready",
            sourceName: "running.pdf",
            status: "RUNNING",
            progress: 45,
            retryable: false,
          },
        ],
      },
    });

    expect(wrapper.get('[data-job="failed"]').text()).toContain("重试");
    expect(wrapper.get('[data-job="running"]').text()).not.toContain("重试");
  });
});

it("失败任务展示原因且不再显示无意义的进度", () => {
  const wrapper = mount(JobTable, {
    props: {
      jobs: [
        { id: "failed", datasetId: "d", sourceName: "bad.md", status: "FAILED", progress: 1, retryable: false, errorMessage: "Embedding 模型鉴权失败：请在设置中更新 API Key 后重试" },
        { id: "running", datasetId: "d", sourceName: "busy.md", status: "RUNNING", progress: 45, retryable: false },
      ],
    },
  });
  const failed = wrapper.get('[data-job="failed"]');
  expect(failed.find("progress").exists()).toBe(false);
  expect(failed.text()).toContain("鉴权失败");
  const running = wrapper.get('[data-job="running"]');
  expect(running.find("progress").exists()).toBe(true);
  expect(running.text()).toContain("45%");
});
