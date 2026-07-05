/**
 * formatChapterCode 单测 — fix-19 收尾 P2 polish(章节语义化)。
 *
 * 覆盖 7 case:ch1 / ch9 / pdf-ch3 / docx-pest / docx-stab-adapt + 2 个 passthrough。
 */
import { describe, it, expect } from 'vitest';
import { formatChapterCode } from '@/utils/formatChapterCode';

describe('formatChapterCode(fix-19 polish)', () => {
  it('finance ch1 → "章节 1"', () => {
    expect(formatChapterCode('ch1')).toBe('章节 1');
  });

  it('finance ch9 → "章节 9"', () => {
    expect(formatChapterCode('ch9')).toBe('章节 9');
  });

  it('corporate_strategy PDF ch3 → "PDF 章节 3"', () => {
    expect(formatChapterCode('pdf-ch3')).toBe('PDF 章节 3');
  });

  it('docx-pest → "PEST 分析"(DOCX 主题 1)', () => {
    expect(formatChapterCode('docx-pest')).toBe('PEST 分析');
  });

  it('docx-stab-adapt → "战略稳定性与文化适应性"(DOCX 主题 4 完整名)', () => {
    expect(formatChapterCode('docx-stab-adapt')).toBe('战略稳定性与文化适应性');
  });

  it('未知 DOCX topic → passthrough(防御硬编码失效)', () => {
    // 未来新加资料时不被硬编码 block,原样回显便于识别
    expect(formatChapterCode('docx-future-topic')).toBe('docx-future-topic');
  });

  it('空字符串 / null → 空字符串(防御渲染崩溃)', () => {
    expect(formatChapterCode('')).toBe('');
    expect(formatChapterCode(null)).toBe('');
    expect(formatChapterCode(undefined)).toBe('');
  });
});
