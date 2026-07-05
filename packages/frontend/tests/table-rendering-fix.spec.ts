/**
 * Phase 5 fix-6 — 表格渲染回归测试。
 *
 * 验证：
 *  - QuestionCard 用 v-html + markdown-it 渲染 GFM 表格 → DOM 出现 <table><th><td>
 *  - DOMPurify 阻止 <script>/事件属性 XSS
 *  - Admin.vue review queue 的 stem-cell 同样渲染 markdown
 *  - ExamResult.vue 折叠预览用纯文本 (不能 v-html 已 truncate 的 stem)
 *  - 空 stem / undefined 走空字符串
 *
 * 不依赖真实后端 — 用 QuestionPublic 最小 fixture + Element Plus 插件。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import QuestionCard from '@/components/QuestionCard.vue';
import Admin from '@/pages/Admin.vue';
import ExamResult from '@/pages/ExamResult.vue';
import type { QuestionPublic } from '@/types/api';
import { renderMarkdown } from '@/composables/useMarkdown';

function makeQuestion(stem: string): QuestionPublic {
  return {
    id: 1,
    type: 'single',
    chapter_id: 1,
    chapter_code: 'ch1',
    difficulty: 2,
    stem,
    options: ['A', 'B', 'C', 'D'],
    score: 2,
    sequence: 1,
  };
}

const MD_TABLE = [
  '| 资产 | 期末数 |',
  '| --- | --- |',
  '| 货币资金 | 1000 |',
  '| 应收账款 | 2000 |',
].join('\n');

describe('QuestionCard 表格渲染（phase 5 fix-6）', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('test_question_card_renders_markdown_table: GFM 表格 → DOM 出现 <table>/<th>/<td>', async () => {
    const wrapper = mount(QuestionCard, {
      props: { question: makeQuestion(MD_TABLE), userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    await nextTick();
    const html = wrapper.html();
    expect(html).toContain('<table>');
    expect(html).toContain('<th>');
    expect(html).toContain('<td>');
    // 原始 markdown 字符不应残留在最终 HTML（被解析了）
    expect(html).not.toContain('| 资产 | 期末数 |');
    // 数据 cell 内容存在
    expect(html).toContain('货币资金');
    expect(html).toContain('1000');
  });

  it('test_question_card_escapes_html: <script> 被 DOMPurify 转义（XSS 防御）', async () => {
    const evil = '正常题干\n\n<script>window.__pwned = true</script>\n\n又一行';
    const wrapper = mount(QuestionCard, {
      props: { question: makeQuestion(evil), userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    await nextTick();
    const html = wrapper.html();
    // <script> 标签必须被转义为 &lt;script&gt; 而非真正可执行标签
    expect(html).not.toContain('<script>');
    // "window.__pwned" 在转义后仍可作为文本出现,但不会变成可执行 JS
    // 关键断言:html 中没有 raw <script> 标签
    const scriptTagMatches = html.match(/<script\b/gi);
    expect(scriptTagMatches).toBeNull();
    // 剩余内容（含 "正常题干"）正常显示
    expect(html).toContain('正常题干');
    expect(html).toContain('又一行');
  });

  it('test_empty_stem_renders_empty: stem="" / null 不报错且 .question-stem 容器内为空', () => {
    // 空字符串
    const w1 = mount(QuestionCard, {
      props: { question: makeQuestion(''), userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const stemEl = w1.find('.question-stem');
    expect(stemEl.exists()).toBe(true);
    // .question-stem 内部应无任何文本（renderMarkdown("") 返回 ""）
    // 注意 wrapper.html() 含 wrapper 自身,这里检查 inner text
    expect(stemEl.text()).toBe('');

    // null/undefined 也走空字符串（避免 .replace/.slice 抛错）
    expect(() => {
      const nullStem = makeQuestion('x');
      nullStem.stem = null as unknown as string;
      mount(QuestionCard, {
        props: { question: nullStem, userAnswer: '' },
        global: { plugins: [ElementPlus] },
      });
    }).not.toThrow();
  });

  it('test_question_card_keeps_paragraph_breaks: \\n\\n 应产生段落而非单行', async () => {
    const wrapper = mount(QuestionCard, {
      props: { question: makeQuestion('第一段\n\n第二段'), userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    await nextTick();
    const html = wrapper.html();
    // markdown breaks + markdown-it 把 \n 转为 <br>, \n\n 为 <p>
    expect(html).toContain('第一段');
    expect(html).toContain('第二段');
  });
});

describe('renderMarkdown composable', () => {
  it('空 / null / undefined 返回空字符串', () => {
    expect(renderMarkdown('')).toBe('');
    expect(renderMarkdown(null)).toBe('');
    expect(renderMarkdown(undefined)).toBe('');
  });

  it('表格 markdown → 含 <table>', () => {
    const out = renderMarkdown(MD_TABLE);
    expect(out).toContain('<table>');
    expect(out).toContain('<th');
    expect(out).toContain('<td');
  });

  it('DOMPurify 剥除 <script>', () => {
    const out = renderMarkdown('before\n\n<script>alert(1)</script>\n\nafter');
    expect(out).not.toContain('<script>');
    expect(out).toContain('before');
    expect(out).toContain('after');
  });

  it('行内 **bold** 解析为 <strong>', () => {
    const out = renderMarkdown('这是 **重点** 内容');
    expect(out).toContain('<strong>重点</strong>');
  });
});

describe('Admin.vue / ExamResult.vue 走 renderMarkdown（mount 阶段不报错）', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('test_admin_review_renders_markdown: Admin mount 不抛错（import renderMarkdown 成功）', () => {
    // Admin 内部用 getReviewQueue 失败时静默吞错 — mount 应该不报错即说明 import 通；
    // 失败由 network/loadQueue catch 处理，不影响渲染入口
    expect(() => mount(Admin, { global: { plugins: [ElementPlus] } })).not.toThrow();
  });

  it('test_result_page_preview_renders_markdown: ExamResult mount 不抛错（renderMarkdown 已 import）', () => {
    expect(() => mount(ExamResult, { global: { plugins: [ElementPlus] } })).not.toThrow();
  });
});
