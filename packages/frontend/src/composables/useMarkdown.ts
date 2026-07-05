/**
 * Markdown 渲染 composable — 题目题干/答案/评语包含 GFM 表格时用。
 *
 * 设计要点：
 * - 后端 stem 是 "raw text + markdown" 混合（preprocessor parse_docx 已是 markdown），
 *   前端不能用 `{{ }}` mustache 转义插值，必须 v-html + sanitize
 * - markdown-it 配置：GFM-like（breaks + typographer），html 关闭防 XSS
 * - DOMPurify 第二层防御：即使 md 引入意外 HTML 也会被剥
 * - 复用单例 md 实例 — markdown-it 初始化有成本（rule 编译），全局一份就够
 */
import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: true,
});

/**
 * 把 markdown 文本渲染成 sanitize 后的 HTML — 供 v-html 直接绑定。
 *
 * 参数：
 *   text — markdown 源文本（可为 null/undefined/空串）
 * 返回：
 *   sanitized HTML 字符串（已 DOMPurify sanitize，可直接 v-html）
 */
export function renderMarkdown(text: string | null | undefined): string {
  if (!text) return '';
  const html = md.render(text);
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
}
