/**
 * 统一 API 客户端（基于 axios + 拦截器）。
 *
 * - 请求拦截：自动注入 Authorization: Bearer <token>
 * - 响应拦截：401 → 清空 token + 跳 /login；其它错误统一抛 ApiError
 * - baseURL：开发用 /api（Vite 代理），生产直连同域
 */
import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';
import type { ApiError } from '@/types/api';

/** localStorage 中 token 的 key。 */
export const TOKEN_KEY = 'fes_token';
/** localStorage 中 role 的 key。 */
export const ROLE_KEY = 'fes_role';

/**
 * 创建 axios 实例。
 */
const instance: AxiosInstance = axios.create({
  baseURL: import.meta.env.DEV ? '/api' : '',
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
});

/**
 * 请求拦截：注入 Bearer token。
 */
instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

/**
 * 响应拦截：401 清 token + 跳 /login?reason=expired，其它错误统一抛 ApiError。
 *
 * 设计动机:fix-bug mixed 模式 UX — 用户选 mixed 后 axios timeout 15s
 * 触发,但实际 mix 后台可能跑 ~30-60s 也可能早就 401(此前 SSE 修复前
 * 误清过 token)。统一处理:任何 401 都强制跳登录页并带 reason=expired
 * 参数,Login.vue 据此弹友好提示。
 */
instance.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<{ detail?: string }>) => {
    const status = err.response?.status ?? 0;
    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ROLE_KEY);
      // 避免在 /login 页无限重定向
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login?reason=expired';
      }
    }
    const message =
      err.response?.data?.detail ?? err.message ?? '网络请求失败';
    const apiErr: ApiError = { status, message };
    return Promise.reject(apiErr);
  },
);

/**
 * 从 axios 错误还原 ApiError 对象的辅助函数。
 */
export function toApiError(err: unknown): ApiError {
  if (err && typeof err === 'object' && 'status' in err && 'message' in err) {
    return err as ApiError;
  }
  return { status: 0, message: String(err) };
}

export default instance;
