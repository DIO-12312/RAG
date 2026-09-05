import { defineStore } from "pinia";

export interface AuthGate {
  readonly isAuthenticated: boolean;
  restore(): Promise<void>;
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    isAuthenticated: false,
  }),
  actions: {
    async restore(): Promise<void> {},
  },
});
