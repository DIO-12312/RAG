import { afterAll, afterEach, beforeAll } from "vitest";

import { resetMockState } from "../src/mocks/handlers";
import { server } from "../src/mocks/server";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  document.body.innerHTML = "";
  server.resetHandlers();
  resetMockState();
});

afterAll(() => {
  server.close();
});
