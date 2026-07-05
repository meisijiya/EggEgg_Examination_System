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
 * 视觉:复用 design tokens(sky/sky-soft/surface-2/border)— 不引入新色板。
 */
import { onMounted, ref, watch } from 'vue';
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

/** 监听 props.modelValue 变化(如父组件重置)。 */
watch(
  () => props.modelValue?.id ?? '',
  (newId) => {
    if (newId) selectedId.value = newId;
  },
);

/**
 * 选中变更处理 — emit + 写 localStorage。
 */
function onChange(newId: string | number | undefined): void {
  if (!newId) return;
  const id = String(newId);
  const sub = subjects.value.find((s) => s.id === id);
  if (!sub) return;
  selectedId.value = id;
  try {
    localStorage.setItem(SUBJECT_STORAGE_KEY, id);
  } catch {
    // 静默
  }
  emit('update:modelValue', sub);
}
</script>

<template>
  <div class="subject-switcher">
    <el-select
      v-model="selectedId"
      size="small"
      :disabled="subjects.length <= 1"
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