import { describe, expect, it } from "vitest";

import { getCurrentUser, login, logout } from "../src/api/auth";
import { ApiError } from "../src/api/http";
import { updateChatSettings } from "../src/api/settings";

describe("mock authentication", () => {
  it("returns AUTH_EXPIRED when an anonymous caller requests the current user", async () => {
    await expect(getCurrentUser()).rejects.toEqual(
      new ApiError(401, "AUTH_EXPIRED", "登录已过期，请重新登录。"),
    );
  });

  it("restores the logged-in user until logout", async () => {
    const user = await login({ email: "demo@example.test", password: "password" });

    await expect(getCurrentUser()).resolves.toEqual(user);
    await logout();
    await expect(getCurrentUser()).rejects.toMatchObject({ code: "AUTH_EXPIRED" });
  });

  it("does not echo a replacement model API key", async () => {
    await login({ email: "demo@example.test", password: "password" });

    const settings = await updateChatSettings({
      baseUrl: "https://api.example.test/v1",
      modelName: "example-chat",
      timeoutSeconds: 30,
      thinkingEnabled: true,
      apiKey: "sk-test-secret",
    });

    expect(JSON.stringify(settings)).not.toContain("sk-test-secret");
    expect(settings.chat.thinkingEnabled).toBe(true);
  });
});
