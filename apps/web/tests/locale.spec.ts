import { setActivePinia, createPinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useLocaleStore } from "../src/stores/locale";

describe("locale preferences", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("defaults to Chinese and can switch to English", () => {
    const locale = useLocaleStore();

    expect(locale.value).toBe("zh-CN");

    locale.set("en-US");

    expect(locale.value).toBe("en-US");
  });
});
