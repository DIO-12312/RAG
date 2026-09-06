import type { CurrentUser, LoginRequest } from "./contracts";
import { request } from "./http";

export function getCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/me");
}

export function login(payload: LoginRequest): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

export function register(payload: LoginRequest): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/register", { method: "POST", body: JSON.stringify(payload) });
}
