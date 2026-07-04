/**
 * SSE 客户端：用 fetch + ReadableStream 解析 Server-Sent Events。
 *
 * 协议约定（与后端 `app/api/explain.py::_sse_format` 对齐）：
 *   - 每个事件是一段 `data: <json>\n\n`（一个或多个 `data:` 行 + 空行分隔）
 *   - 终止：`reader.read()` 返回 `done=true`，或服务端关流
 *
 * ponytail: 用 fetch + ReadableStream 而不是 EventSource —
 *   - EventSource 只支持 GET，AI 讲解是 POST
 *   - EventSource 不支持自定义请求头（没法带 Authorization）
 *   - fetch + ReadableStream 是浏览器原生方案，零依赖
 *
 * 参数:
 *   url      — 相对或绝对 URL（建议走 `/api` 前缀，与 axios client 一致）
 *   options  — fetch 选项（method / headers / body）
 *
 * 返回:
 *   AsyncGenerator，逐个 yield 出 `{data: <json>}` 中解析后的对象。
 *   - 遇到 `data: [DONE]` 直接跳过（兼容 OpenAI 风格的结束标记）
 *   - 单行 JSON 解析失败 → 静默跳过该行（不中断流）
 */
export async function* fetchSSE(
  url: string,
  options: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  } = {},
): AsyncGenerator<Record<string, unknown>> {
  const resp = await fetch(url, {
    method: options.method ?? 'GET',
    headers: {
      Accept: 'text/event-stream',
      ...(options.headers ?? {}),
    },
    body: options.body,
  });
  if (!resp.ok) {
    throw new Error(`SSE HTTP ${resp.status}`);
  }
  if (!resp.body) {
    throw new Error('No response body');
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 事件以空行 `\n\n` 分隔；逐个事件切出后清空 buffer
    let sepIdx: number;
    while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
      const event = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + 2);
      // 单个事件可能有多行 `data:`，按 SSE 规范应 join 起来；当前后端只发单行
      for (const line of event.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;
        try {
          yield JSON.parse(payload);
        } catch {
          /* skip malformed chunk — 不让一行坏数据终止整个流 */
        }
      }
    }
  }
}