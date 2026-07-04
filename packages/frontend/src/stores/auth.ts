/**
 * 鉴权 store — 登录态 + JWT 持久化。
 *
 * 设计：
 * - token / role 持久化在 localStorage
 * - 启动时从 localStorage 恢复（满足 F5 刷新仍登录）
 * - 注销时清 localStorage + 跳 /login
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { login as apiLogin } from '@/api';
import { ROLE_KEY, TOKEN_KEY } from '@/api/client';
import type { UserRole } from '@/types/api';

export const useAuthStore = defineStore('auth', () => {
  // ---------- state ----------
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '');
  const role = ref<UserRole | null>(
    (localStorage.getItem(ROLE_KEY) as UserRole) || null,
  );

  // ---------- getters ----------
  const isAuthenticated = computed(() => !!token.value);
  const isAdmin = computed(() => role.value === 'admin');

  // ---------- actions ----------

  /**
   * 登录（密码登录）— 成功后写 token + role 到 store 和 localStorage。
   */
  async function login(password: string): Promise<void> {
    const resp = await apiLogin(password);
    token.value = resp.access_token;
    role.value = resp.role;
    localStorage.setItem(TOKEN_KEY, resp.access_token);
    localStorage.setItem(ROLE_KEY, resp.role);
  }

  /**
   * 注销 — 清 token + role 并跳 /login。
   */
  function logout(): void {
    token.value = '';
    role.value = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
  }

  return { token, role, isAuthenticated, isAdmin, login, logout };
});
