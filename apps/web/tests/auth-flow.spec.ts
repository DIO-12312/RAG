import { setActivePinia, createPinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "../src/stores/auth";

describe("authentication flow", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("clears its user and exposes an expiration notice when restore is unauthorized", async () => {
    const auth = useAuthStore();

    await auth.restore();

    expect(auth.isAuthenticated).toBe(false);
    expect(auth.expirationNotice).toContain("登录已过期");
  });
});
