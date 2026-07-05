<script setup lang="ts">
/**
 * 顶栏 SubjectSwitcher — 全局科目切换组件。
 *
 * Props:
 *   modelValue — v-model 绑定的当前选中 Subject(可为 null,代表未选)
 *
 * Emits:
 *   update:modelValue — 切换时通知父组件
 *
 * 数据流:
 *   - 挂载时 listSubjects() → 列表
 *   - 默认值优先取 props.modelValue,否则读 localStorage('fes_last_subject_id')
 *   - 用户切换时 emit + 写 localStorage
 *   - 后端未就绪时 listSubjects 兜底返回单科目,UI 仍可用
 *
 * Phase 5 fix-4: 单科目时改为显示静态 label(不再用 :disabled 灰态),
 *   让用户清楚"当前仅 1 个科目可选",而非误以为是 UI 坏了。
 *   多科目时仍走 el-select dropdown。
 *
 * 视觉:复用 design tokens(sky/sky-soft/surface-2/border)— 不引入新色板。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { listSubjects, SUBJECT_STORAGE_KEY } from '@/api/subjects';
import type { Subject } from '@/types/api';

interface Props {
  modelValue: Subject | null;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: null,
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: Subject): void;
}>();

/** 后端返回的科目列表。 */
const subjects = ref<Subject[]>([]);
/** 当前 UI 选中的科目 id(与 el-select v-model 对齐)。 */
const selectedId = ref<string>(props.modelValue?.id ?? '');

/**
 * Phase 5 fix-4: 单科目时改为静态标签(非禁用 dropdown)。
 *
 * 原因:后端 /api/subjects 端点未实现,listSubjects 兜底永远返回 1 个科目,
 * 此时 :disabled 让 dropdown 灰态,用户误以为是 UI 坏;改成 el-tag 静态显示
 * 后,用户清楚看到"当前可选:财务管理"且无切换入口。
 *
 * 多科目时(el-select 路径)保持原 dropdown 行为不变。
 */
const showAsLabel = computed<boolean>(() => subjects.value.length <= 1);

onMounted(async () => {
  subjects.value = await listSubjects();
  // 优先级:props.modelValue > localStorage > 列表第一项
  if (!selectedId.value) {
    try {
      const cached = localStorage.getItem(SUBJECT_STORAGE_KEY);
      if (cached && subjects.value.some((s) => s.id === cached)) {
        selectedId.value = cached;
      }
    } catch {
      // 静默
    }
  }
  if (!selectedId.value && subjects.value.length > 0) {
    selectedId.value = subjects.value[0].id;
  }
  // 初始化时如果父组件没有值,emit 一次让外部同步
  if (!props.modelValue && selectedId.value) {
    const sub = subjects.value.find((s) => s.id === selectedId.value);
    if (sub) emit('update:modelValue', sub);
  }
});

/**
 * 监听父组件 modelValue 变化(如父组件重置 / setSubject)。
 * Phase 5 fix-4: 加 immediate: true,首屏 props 已有值时也能同步 selectedId
 * (之前 watch 默认懒触发,如果父组件在 setup 之后立即 setSubject 不会被感知)。
 */
watch(
  () => props.modelValue?.id ?? '',
  (newId) => {
    if (newId) selectedId.value = newId;
  },
  { immediate: true },
);

/**
 * 选中变更处理 — emit + 写 localStorage。
 *
 * Phase 5 fix-4: 强化防御 — 即便 onChange 被 EP 用新值触发,仍走完整链路
 * (find / set / persist / emit),任一环节出错都不会破坏状态一致性。
 */
function onChange(newId: string | number | undefined): void {
  if (newId === undefined || newId === null || newId === '') return;
  const id = String(newId);
  const sub = subjects.value.find((s) => s.id === id);
  if (!sub) return;
  selectedId.value = id;
  try {
    localStorage.setItem(SUBJECT_STORAGE_KEY, id);
  } catch {
    // 静默 — 隐私模式 / quota 满不影响当前会话
  }
  emit('update:modelValue', sub);
}
</script>

<template>
  <div class="subject-switcher">
    <!-- Phase 5 fix-4: 单科目时显示静态 label,不再用禁用 dropdown 误导用户 -->
    <span v-if="showAsLabel" class="subject-label" :title="subjects[0]?.name">
      <span class="subject-label-icon" aria-hidden="true">📚</span>
      <span class="subject-label-text">{{ subjects[0]?.name ?? '财务管理' }}</span>
    </span>

    <el-select
      v-else
      v-model="selectedId"
      size="small"
      @change="onChange"
    >
      <el-option
        v-for="sub in subjects"
        :key="sub.id"
        :value="sub.id"
        :label="sub.name"
      />
    </el-select>
  </div>
</template>

<style scoped>
/* 顶栏紧凑型 — 不抢 logo 视觉重心 */
.subject-switcher {
  display: inline-flex;
  align-items: center;
}

/* Phase 5 fix-4: 单科目静态 label — sky-soft 胶囊 + emoji */
.subject-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  background: var(--sky-soft);
  color: var(--sky-active);
  border: 1px solid var(--sky-fog);
  border-radius: var(--r-pill);
  font: 500 var(--fs-body) / 1.4 var(--font-body);
  user-select: none;
  cursor: default;
  white-space: nowrap;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.subject-label-icon {
  font-size: 14px;
  line-height: 1;
}
.subject-label-text {
  font-weight: var(--fw-medium);
}

.subject-switcher :deep(.el-select) {
  min-width: 140px;
}

.subject-switcher :deep(.el-select__wrapper) {
  background: var(--surface-2);
  border-radius: var(--r-md);
  box-shadow: 0 0 0 1px var(--border) !important;
}

.subject-switcher :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1.5px var(--sky) !important;
  background: var(--surface);
}
</style>