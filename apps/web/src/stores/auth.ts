import { defineStore } from "pinia";

import { getCurrentUser, login as loginRequest, logout as logoutRequest } from "@/api/auth";
import type { CurrentUser, LoginRequest } from "@/api/contracts";
import { ApiError } from "@/api/http";

export interface AuthGate {
  readonly isAuthenticated: boolean;
  readonly isAdmin?: boolean;
  restore(): Promise<void>;
  refresh?(): Promise<void>;
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as CurrentUser | null,
    expirationNotice: "",
  }),
  getters: {
    isAuthenticated: (state) => state.user !== null,
    isAdmin: (state) => state.user?.role === "admin",
  },
  actions: {
    async refresh(): Promise<void> {
      try { this.user = await getCurrentUser(); this.expirationNotice = ""; }
      catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.user = null;
          this.expirationNotice = error.message;
        }
        throw error;
      }
    },
    async restore(): Promise<void> {
      if (this.user) return;
      try { await this.refresh(); }
      catch {
        // Unauthenticated public routes may continue without a session.
      }
    },
    async login(payload: LoginRequest): Promise<void> { this.user = await loginRequest(payload); this.expirationNotice = ""; },
    async logout(): Promise<void> { await logoutRequest(); this.user = null; },
  },
});
