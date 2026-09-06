import { defineStore } from "pinia";

import { getCurrentUser, login as loginRequest, logout as logoutRequest } from "@/api/auth";
import type { CurrentUser, LoginRequest } from "@/api/contracts";
import { ApiError } from "@/api/http";

export interface AuthGate {
  readonly isAuthenticated: boolean;
  restore(): Promise<void>;
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as CurrentUser | null,
    expirationNotice: "",
  }),
  getters: { isAuthenticated: (state) => state.user !== null },
  actions: {
    async restore(): Promise<void> {
      if (this.user) return;
      try { this.user = await getCurrentUser(); this.expirationNotice = ""; }
      catch (error) {
        this.user = null;
        if (error instanceof ApiError && error.code === "AUTH_EXPIRED") this.expirationNotice = error.message;
      }
    },
    async login(payload: LoginRequest): Promise<void> { this.user = await loginRequest(payload); this.expirationNotice = ""; },
    async logout(): Promise<void> { await logoutRequest(); this.user = null; },
  },
});
