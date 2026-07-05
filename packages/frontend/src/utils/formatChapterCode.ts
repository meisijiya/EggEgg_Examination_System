/**
 * 全局 chapter code 语义化显示(fix-19 收尾 polish)。
 *
 * 后端 schema v8 多来源:
 *  - finance 科目: ch1 ~ ch9 (单层)
 *  - corporate_strategy PDF: 由 fix-22 parse_questions.py 推断 → "pdf-chN"
 *  - corporate_strategy DOCX (6 DOCX 资料, AI 出题 reference):
 *      docx-pest / docx-corp / docx-empirical
 *      docx-stab-adapt / docx-choice-impl / docx-innovation-subj
 *
 * 学员视角统一翻译成中文标签(章节 N / PDF 章节 N / 资料主题名);
 * 未知 code passthrough 以避免新未来加资源时硬编码失败。
 */
const DOCX_TOPIC_LABELS: Record<string, string> = {
  pest: 'PEST 分析',
  corp: '企业战略',
  empirical: '实证研究结构框架',
  'stab-adapt': '战略稳定性与文化适应性',
  'choice-impl': '战略选择与实施',
  'innovation-subj': '探索战略创新主观题',
};

/**
 * 把后端 chapter.code 转成中文显示。
 *
 * "ch1"        → "章节 1"
 * "ch9"        → "章节 9"
 * "pdf-ch3"    → "PDF 章节 3"
 * "docx-pest"  → "PEST 分析"
 * "docx-stab-adapt" → "战略稳定性与文化适应性"
 * "unknown"   → "unknown" (passthrough)
 *
 * 参数:
 *   code: 后端 chapter.code 字符串(可能含子分类标记)
 * 返回:
 *   中文友好显示文本;未知 code 原样返回(便于运维识别)
 */
export function formatChapterCode(code: string | null | undefined): string {
  if (!code) return '';
  // 1) finance 科目原生章节
  if (/^ch\d+$/.test(code)) {
    return `章节 ${code.slice(2)}`;
  }
  // 2) corporate_strategy PDF 来源章节
  const pdfMatch = code.match(/^pdf-ch(\d+)$/);
  if (pdfMatch) {
    return `PDF 章节 ${pdfMatch[1]}`;
  }
  // 3) corporate_strategy DOCX 资料主题(pdf_chX 之外的归在这里)
  const docxMatch = code.match(/^docx-(.+)$/);
  if (docxMatch) {
    const topic = docxMatch[1];
    return DOCX_TOPIC_LABELS[topic] ?? code;
  }
  // 4) 未知 → passthrough(便于未来扩展时不被硬编码 block)
  return code;
}
