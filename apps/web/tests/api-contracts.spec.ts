import { describe, expect, it } from "vitest";

import { rootRoutes } from "../src/api/contracts";
import { mockSettings } from "../src/mocks/data";

describe("frontend API contracts", () => {
  it("never exposes a model API key in a settings response", () => {
    const serialized = JSON.stringify(mockSettings.chat);

    expect(serialized).not.toContain("sk-");
    expect(mockSettings.chat.apiKeyConfigured).toBe(true);
    expect(mockSettings.chat.apiKeyHint).toBe("****8Kp2");
  });

  it("uses the approved root routes without an API version prefix", () => {
    expect(rootRoutes).toEqual([
      "/auth/register",
      "/auth/login",
      "/auth/logout",
      "/me",
      "/datasets",
      "/datasets/:id",
      "/datasets/:id/documents",
      "/datasets/:id/jobs",
      "/jobs/:id/cancel",
      "/jobs/:id/retry",
      "/documents/:id",
      "/documents/:id/source-topic",
      "/settings",
      "/settings/models/chat",
      "/settings/models/embedding",
      "/settings/models/rerank",
      "/settings/agent",
      "/chat/stream",
    ]);
    expect(rootRoutes.every((route) => !route.startsWith("/api/"))).toBe(true);
  });
});
