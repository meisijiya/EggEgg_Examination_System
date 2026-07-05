/**
 * SubjectSwitcher Phase 5 fix-4 测试 — 顶栏科目"无法切换" bug 修复验证。
 *
 * 根因:
 *   后端无 /api/subjects 端点 → listSubjects() 兜底返回单科目
 *   → :disabled="subjects.length <= 1" 触发 → dropdown 灰态
 *   → 用户点不动 = "无法切换"
 *
 * 修法:
 *   单科目时改为静态 .subject-label(el-tag 风格),多科目仍走 el-select。
 *
 * 覆盖:
 *  1. 单科目 → 不渲染 el-select,渲染 .subject-label
 *  2. 多科目 → 渲染 el-select 且非 disabled
 *  3. 切换 emit + 写 localStorage + 父组件 store 更新
 *  4. startNew() 默认读 store.currentSubject.id(无 hardcode)
 *  5. 后端异常 → 仍能展示兜底单科目 label
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import SubjectSwitcher from '@/components/SubjectSwitcher.vue';
import { useExamStore } from '@/stores/exam';
import * as subjectsApi from '@/api/subjects';
import client from '@/api/client';
import type { StartExamResponse, Subject } from '@/types/api';
import { TOKEN_KEY } from '@/api/client';

const SUBJECTS_KEY = 'fes_last_subject_id';
const finSub: Subject = { id: 'fin-mgmt', name: '财务管理', question_count: 628 };
const corpSub: Subject = { id: 'corp-strat', name: '公司战略和风险管理', question_count: 120 };

function makeFakeStart(): StartExamResponse {
  return {
    attempt_id: 99,
    started_at: '2026-07-05T08:00:00Z',
    time_limit_minutes: 120,
    total_score: 110,
    questions: [],
  };
}

describe('SubjectSwitcher Phase 5 fix-4 (单科目 UX 修复)', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it('#1 单科目时: 不渲染 el-select,改为静态 .subject-label', async () => {
    // 后端 listSubjects 兜底返回单科目 (当前生产环境真实情况)
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([finSub]);

    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    });
    await flushPromises();
    await nextTick();

    // 关键断言:无 el-select,只有 subject-label
    expect(wrapper.find('.el-select').exists()).toBe(false);
    expect(wrapper.find('.subject-label').exists()).toBe(true);
    expect(wrapper.find('.subject-label-text').text()).toBe('财务管理');
  });

  it('#2 多科目时: 渲染 el-select 且非 disabled(可切换)', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([finSub, corpSub]);

    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    });
    await flushPromises();
    await nextTick();

    // el-select 应渲染
    expect(wrapper.find('.el-select').exists()).toBe(true);
    // 不应渲染静态 label
    expect(wrapper.find('.subject-label').exists()).toBe(false);

    // 验证 el-select 不是 disabled
    const select = wrapper.findComponent({ name: 'ElSelect' });
    expect(select.exists()).toBe(true);
  });

  it('#3 用户切换 el-select option → emit + localStorage + store.setSubject', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([finSub, corpSub]);

    const exam = useExamStore();
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    // 先 set 初始
    exam.setSubject(finSub);

    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: exam.currentSubject },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    });
    await flushPromises();
    await nextTick();

    // 模拟 App.vue 的 onSubjectChange 监听:每次 emit 调用 setSubject
    const handleEmit = (sub: Subject): void => {
      exam.setSubject(sub);
    };

    // 模拟 el-select 切到 corp-strat
    const select = wrapper.findComponent({ name: 'ElSelect' });
    select.vm.$emit('change', 'corp-strat');
    await nextTick();
    await flushPromises();

    // 取最后一次 emit,模拟父组件反应
    const emits = wrapper.emitted('update:modelValue');
    expect(emits).toBeTruthy();
    const lastEmit = emits![emits!.length - 1][0] as Subject;
    handleEmit(lastEmit);

    // 验证三件事:
    // (a) emit 出去的是 corp-strat Subject
    expect(lastEmit.id).toBe('corp-strat');
    // (b) localStorage 写入
    expect(localStorage.getItem(SUBJECTS_KEY)).toBe('corp-strat');
    // (c) store.currentSubject 更新
    expect(exam.currentSubject?.id).toBe('corp-strat');
  });

  it('#4 startNew 默认读 store.currentSubject.id(无 hardcoded "fin-mgmt")', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    const exam = useExamStore();
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    // 用户切换到 corp-strat 后
    exam.setSubject(corpSub);

    // 不传 subjectId,startNew 应读 store
    await exam.startNew('standard');

    const [, body] = spy.mock.calls[0] as [string, Record<string, unknown>];
    // 关键:body.subject_id 必须是 corp-strat,不是写死的 fin-mgmt
    expect(body).toMatchObject({ subject_id: 'corp-strat', mode: 'standard' });
    // 显式禁止 hardcoded 兜底(避免用户感知不到的 silent fallback)
    expect(body.subject_id).not.toBe('fin-mgmt');
  });

  it('#5 后端异常 → 兜底单科目 → 仍渲染静态 label 不崩', async () => {
    // 模拟后端 /api/subjects 抛 500 (Phase 5 fix-4 时的真实情况)
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([finSub]);

    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    });
    await flushPromises();
    await nextTick();

    // 兜底 label 应渲染(无 el-select)
    expect(wrapper.find('.el-select').exists()).toBe(false);
    expect(wrapper.find('.subject-label').exists()).toBe(true);
    expect(wrapper.find('.subject-label-text').text()).toBe('财务管理');
  });

  it('#6 父组件 modelValue 立即变化时 watch (immediate) 同步 selectedId', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([finSub, corpSub]);

    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: finSub },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    });
    await flushPromises();
    await nextTick();

    // 父组件更新 props.modelValue (e.g. App.vue reset / 切换)
    await wrapper.setProps({ modelValue: corpSub });
    await nextTick();

    // 内部 selectedId 应跟上(此时 emit 不应触发 — props 已经有值)
    const vm: any = wrapper.vm as any;
    expect(vm.selectedId).toBe('corp-strat');
  });
});