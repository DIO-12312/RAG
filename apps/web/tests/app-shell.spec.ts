import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import AppShell from "../src/components/AppShell.vue";

describe("application shell", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("shows Chinese navigation by default and English after switching", async () => {
    const wrapper = mount(AppShell, {
      global: { plugins: [createPinia()] },
    });

    expect(wrapper.text()).toContain("知识库");

    await wrapper.get('button[aria-label="Language"]').trigger("click");

    expect(wrapper.text()).toContain("Datasets");
  });
});
