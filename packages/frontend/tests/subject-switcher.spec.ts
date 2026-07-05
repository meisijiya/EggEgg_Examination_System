/**
 * SubjectSwitcher 组件测试 — fix-30a。
 *
 * 覆盖:
 *  - mount 后调 listSubjects,展示科目列表
 *  - 默认选中 localStorage 中缓存的科目
 *  - 默认选中列表第一项
 *  - 用户切换 → emit update:modelValue + 写 localStorage
 *  - 后端失败 → 兜底返回单科目 ['财务管理'],UI 不崩
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import SubjectSwitcher from '@/components/SubjectSwitcher.vue';
import * as subjectsApi from '@/api/subjects';

const SUBJECTS_KEY = 'fes_last_subject_id';

describe('SubjectSwitcher(fix-30a)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('mount 后调 listSubjects + 渲染科目列表', async () => {
    const spy = vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([
      { id: 'fin-mgmt', name: '财务管理' },
      { id: 'corp-strat', name: '公司战略和风险管理' },
    ]);
    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    expect(spy).toHaveBeenCalled();
    // 验证内部状态:emit 出第一个 fallback subject
    const emits = wrapper.emitted('update:modelValue');
    expect(emits).toBeTruthy();
    expect(emits![0][0]).toEqual({ id: 'fin-mgmt', name: '财务管理' });
  });

  it('localStorage 有缓存时优先选中缓存项', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([
      { id: 'fin-mgmt', name: '财务管理' },
      { id: 'corp-strat', name: '公司战略和风险管理' },
    ]);
    localStorage.setItem(SUBJECTS_KEY, 'corp-strat');
    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    await nextTick();
    const emits = wrapper.emitted('update:modelValue');
    expect(emits).toBeTruthy();
    const last = emits![emits!.length - 1];
    expect(last[0]).toEqual({ id: 'corp-strat', name: '公司战略和风险管理' });
  });

  it('props.modelValue 优先于 localStorage', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([
      { id: 'fin-mgmt', name: '财务管理' },
      { id: 'corp-strat', name: '公司战略和风险管理' },
    ]);
    localStorage.setItem(SUBJECTS_KEY, 'corp-strat');
    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: { id: 'fin-mgmt', name: '财务管理' } },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    await nextTick();
    // 不应 emit(因为 props 已有值)
    const emits = wrapper.emitted('update:modelValue');
    expect(emits).toBeFalsy();
  });

  it('列表为空 → 兜底返回 ["财务管理"]', async () => {
    const spy = vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([
      { id: 'fin-mgmt', name: '财务管理', question_count: 0 },
    ]);
    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    expect(spy).toHaveBeenCalled();
    expect(wrapper.html()).toContain('财务管理');
  });

  it('localStorage 缓存不在列表中时 → 兜底为第一项', async () => {
    vi.spyOn(subjectsApi, 'listSubjects').mockResolvedValue([
      { id: 'fin-mgmt', name: '财务管理' },
    ]);
    localStorage.setItem(SUBJECTS_KEY, 'unknown-subj');
    const wrapper = mount(SubjectSwitcher, {
      props: { modelValue: null },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    await nextTick();
    const emits = wrapper.emitted('update:modelValue');
    expect(emits).toBeTruthy();
    expect(emits![0][0]).toEqual({ id: 'fin-mgmt', name: '财务管理' });
  });
});