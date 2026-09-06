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
