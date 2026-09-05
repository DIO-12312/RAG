import { defineStore } from "pinia";

export type Locale = "zh-CN" | "en-US";

export const useLocaleStore = defineStore("locale", {
  state: (): { value: Locale } => ({
    value: "zh-CN",
  }),
  actions: {
    set(value: Locale): void {
      this.value = value;
    },
  },
});
