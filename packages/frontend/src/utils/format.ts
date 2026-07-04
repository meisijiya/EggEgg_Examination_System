/**
 * 全局日期格式化 — 强制上海时区。
 *
 * 业务背景（fix-22）：
 * - 后端统一用 `datetime.utcnow().isoformat() + "Z"` 写库（UTC ISO）
 * - 前端展示必须转 Shanghai，否则学员看到的时间会偏移 8 小时
 * - 用 dayjs + utc + timezone plugin 做安全转换（不用 Date.toLocaleString，
 *   它在某些 SSR / 旧浏览器上会 fallback 到 UTC）
 */
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import timezone from 'dayjs/plugin/timezone';

// 模块加载时一次性 extend — 整个 app 复用同一 plugin 实例。
dayjs.extend(utc);
dayjs.extend(timezone);

/** 全局默认时区 — 改这里就改全站。 */
export const DISPLAY_TZ = 'Asia/Shanghai';

/**
 * 把 ISO 时间字符串（含/不含 Z 后缀）格式化为 Shanghai 时区的可读字符串。
 *
 * 参数:
 *   iso: 后端返回的时间字符串；null/undefined/空串 → 返回 ''
 * 返回:
 *   'YYYY-MM-DD HH:mm:ss' 形式；解析失败 → 返回原始字符串（兜底）
 *
 * ponytail: 解析失败时不抛错、不返回 'Invalid Date'。这是 UI 边界，
 * 兜底显示原文好过显示 'Invalid Date' 让用户怀疑系统。
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const parsed = dayjs.utc(iso);
  if (!parsed.isValid()) {
    // 后端历史数据可能有 'YYYY-MM-DDTHH:MM:SSZ' / 'YYYY-MM-DD HH:MM:SS' 混用
    const fallback = dayjs(iso);
    if (!fallback.isValid()) return iso;
    return fallback.tz(DISPLAY_TZ).format('YYYY-MM-DD HH:mm:ss');
  }
  return parsed.tz(DISPLAY_TZ).format('YYYY-MM-DD HH:mm:ss');
}

/**
 * 短日期格式 — 仅日期，Shanghai 时区。
 *
 * 参数:
 *   iso: 后端返回的时间字符串；null/undefined/空串 → 返回 ''
 * 返回:
 *   'YYYY-MM-DD' 形式
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const parsed = dayjs.utc(iso);
  if (!parsed.isValid()) return iso;
  return parsed.tz(DISPLAY_TZ).format('YYYY-MM-DD');
}