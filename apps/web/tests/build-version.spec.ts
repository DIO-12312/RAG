import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("build version", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses the commit injected at build time and shortens it for display", async () => {
    vi.stubEnv("VITE_GIT_COMMIT", "386f681e639c325883dc39bb3a87a4797c5fe07b");

    const { buildCommit, shortCommit } = await import("../src/utils/version");

    expect(buildCommit).toBe("386f681e639c325883dc39bb3a87a4797c5fe07b");
    expect(shortCommit()).toBe("386f681");
  });

  it("falls back to unknown when no commit was injected", async () => {
    vi.stubEnv("VITE_GIT_COMMIT", "");

    const { buildCommit, shortCommit } = await import("../src/utils/version");

    expect(buildCommit).toBe("unknown");
    expect(shortCommit()).toBe("unknown");
  });

  it("renders the short revision in the sidebar so a deployment can be identified", async () => {
    vi.stubEnv("VITE_GIT_COMMIT", "abcdef1234567890abcdef1234567890abcdef12");

    const AppShell = (await import("../src/components/AppShell.vue")).default;
    const wrapper = mount(AppShell, {
      global: {
        plugins: [createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });

    expect(wrapper.get(".build-version").text()).toContain("abcdef1");
    expect(wrapper.get(".build-version").attributes("title")).toContain(
      "abcdef1234567890abcdef1234567890abcdef12",
    );
  });
});
