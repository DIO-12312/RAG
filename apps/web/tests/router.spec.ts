import { describe, expect, it } from "vitest";

import { createAppRouter } from "../src/router";

describe("application routing", () => {
  it("redirects an anonymous visitor from datasets to login", async () => {
    const router = createAppRouter();

    await router.push("/datasets");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("login");
  });
});
