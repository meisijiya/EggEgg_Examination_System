<script setup lang="ts">
/**
 * /admin 管理员页 — 题目 review（独立鉴权）。
 *
 * 流程：
 *  - 如果 store 中 role !== 'admin' → 显示"管理员登录"表单（复用 /auth/login）
 *  - 登录成功后展示 review queue
 *  - 单题修改：answer / key_points / analysis / difficulty
 *  - 提交 → POST /admin/review/questions/{id}
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { getReviewQueue, updateQuestion } from '@/api';
import { useAuthStore } from '@/stores/auth';
import type { ReviewQueueItem, ReviewUpdateRequest } from '@/types/api';

const auth = useAuthStore();

const queue = ref<ReviewQueueItem[]>([]);
const loading = ref<boolean>(false);
const password = ref<string>('');
const loggingIn = ref<boolean>(false);
const editing = ref<ReviewQueueItem | null>(null);
const editDialogVisible = ref<boolean>(false);
const editForm = ref<ReviewUpdateRequest>({});
const saving = ref<boolean>(false);

const isAdmin = computed(() => auth.isAdmin);

onMounted(async () => {
  if (isAdmin.value) {
    await loadQueue();
  }
});

async function handleLogin(): Promise<void> {
  if (!password.value) {
    ElMessage.warning('请输入密码');
    return;
  }
  loggingIn.value = true;
  try {
    // 用同一个 /auth/login — 后端按密码区分 role
    await auth.login(password.value);
    if (!auth.isAdmin) {
      ElMessage.error('该密码不是管理员密码');
      auth.logout();
      return;
    }
    ElMessage.success('管理员登录成功');
    await loadQueue();
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '登录失败';
    ElMessage.error(msg);
  } finally {
    loggingIn.value = false;
  }
}

async function loadQueue(): Promise<void> {
  loading.value = true;
  try {
    const r = await getReviewQueue();
    queue.value = r.items;
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '加载失败';
    ElMessage.error(msg);
  } finally {
    loading.value = false;
  }
}

function openEdit(item: ReviewQueueItem): void {
  editing.value = item;
  editForm.value = {
    answer: item.answer,
    key_points: item.key_points ? [...item.key_points] : null,
    analysis: null,
    difficulty: item.difficulty,
  };
  editDialogVisible.value = true;
}

function closeEdit(): void {
  editDialogVisible.value = false;
  editing.value = null;
  editForm.value = {};
}

async function saveEdit(): Promise<void> {
  if (!editing.value) return;
  saving.value = true;
  try {
    const resp = await updateQuestion(editing.value.id, editForm.value);
    ElMessage.success(`已更新字段: ${resp.updated_fields.join(', ') || '无'}`);
    closeEdit();
    await loadQueue();
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '保存失败';
    ElMessage.error(msg);
  } finally {
    saving.value = false;
  }
}

/** key_points 编辑（数组转字符串）。 */
const keyPointsText = computed({
  get: () => (editForm.value.key_points ?? []).join('\n'),
  set: (val: string) => {
    editForm.value = {
      ...editForm.value,
      key_points: val.split('\n').map((s) => s.trim()).filter(Boolean),
    };
  },
});

function handleLogout(): void {
  auth.logout();
  queue.value = [];
}
</script>

<template>
  <div class="page-container">
    <!-- 未登录 / 非 admin：登录表单 -->
    <div v-if="!isAdmin" class="admin-login">
      <el-card class="login-card" shadow="always">
        <template #header>
          <div class="login-title">🛠️ 管理员登录</div>
          <div class="login-subtitle">独立密码 · 仅开发期使用</div>
        </template>
        <el-form @submit.prevent="handleLogin">
          <el-form-item label="管理员密码">
            <el-input
              v-model="password"
              type="password"
              show-password
              size="large"
              autocomplete="current-password"
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-button
            type="warning"
            size="large"
            :loading="loggingIn"
            style="width: 100%"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form>
      </el-card>
    </div>

    <!-- 已登录：review queue -->
    <div v-else>
      <div class="admin-header">
        <h1>🛠️ 题目 Review</h1>
        <div class="admin-actions">
          <el-tag type="warning" size="large">
            待 review: {{ queue.length }} 题
          </el-tag>
          <el-button @click="loadQueue" :loading="loading">🔄 刷新</el-button>
          <el-button type="danger" plain @click="handleLogout">退出</el-button>
        </div>
      </div>

      <div v-if="queue.length === 0 && !loading" class="empty-state">
        🎉 题目库无风险项，无需 review
      </div>

      <el-table v-else :data="queue" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="type" label="题型" width="100" />
        <el-table-column prop="chapter_code" label="章节" width="100" />
        <el-table-column prop="difficulty" label="难度" width="80" />
        <el-table-column label="风险标签" width="240">
          <template #default="{ row }">
            <el-tag
              v-for="f in row.flags"
              :key="f"
              type="danger"
              size="small"
              style="margin-right: 4px; margin-bottom: 4px;"
            >
              {{ f }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="题干">
          <template #default="{ row }">
            <div class="stem-cell">{{ row.stem }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="answer" label="答案" width="80" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openEdit(row)">
              修正
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Edit Dialog -->
    <el-dialog
      v-model="editDialogVisible"
      :title="`修正题目 #${editing?.id}`"
      width="600px"
      :close-on-click-modal="false"
      @close="closeEdit"
    >
      <el-form v-if="editing" label-position="top">
        <el-form-item label="题干（只读）">
          <div class="readonly-stem">{{ editing.stem }}</div>
        </el-form-item>
        <el-form-item label="答案">
          <el-input v-model="editForm.answer" placeholder="如：A / ABD / 对" />
        </el-form-item>
        <el-form-item label="难度 (1=简单 / 2=中等 / 3=困难)">
          <el-select v-model="editForm.difficulty" placeholder="不修改">
            <el-option :value="1" label="1 - 简单" />
            <el-option :value="2" label="2 - 中等" />
            <el-option :value="3" label="3 - 困难" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键要点 (每行一个，仅主观题)">
          <el-input
            v-model="keyPointsText"
            type="textarea"
            :rows="4"
            placeholder="计算 / 综合题必填，每行一个要点"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="closeEdit">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 管理员 — login form + review queue table */
.admin-login {
  max-width: 420px;
  margin: var(--s-16) auto;
}
.login-card {
  border-radius: var(--r-xl) !important;
  border: 1px solid var(--border) !important;
  box-shadow: var(--shadow-lg) !important;
}
.login-title {
  font: var(--fw-bold) var(--fs-h2) / var(--lh-tight) var(--font-display);
  text-align: center;
  color: var(--fg);
}
.login-subtitle {
  font-size: var(--fs-body);
  color: var(--muted);
  text-align: center;
  margin-top: var(--s-2);
}

.admin-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--s-4);
  flex-wrap: wrap;
  gap: var(--s-3);
}
.admin-header h1 {
  margin: 0;
  font: var(--fw-semibold) var(--fs-h1) / var(--lh-tight) var(--font-display);
  color: var(--fg);
  letter-spacing: -0.02em;
}
.admin-actions {
  display: flex;
  gap: var(--s-2);
  align-items: center;
  flex-wrap: wrap;
}

.empty-state {
  background: var(--success-soft);
  color: var(--success);
  text-align: center;
  padding: var(--s-10) var(--s-5);
  border-radius: var(--r-lg);
  font-size: var(--fs-body-lg);
  border: 1px dashed var(--success);
}

.stem-cell {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  color: var(--fg);
}

.readonly-stem {
  background: var(--surface-2);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  white-space: pre-wrap;
  max-height: 120px;
  overflow-y: auto;
  color: var(--fg);
  border-left: 3px solid var(--border-strong);
}

/* table 卡片化 */
.section-card :deep(.el-table) {
  --el-table-border-color: var(--border-soft);
  --el-table-header-bg-color: var(--surface-2);
}
.section-card :deep(.el-table th.el-table__cell) {
  background: var(--surface-2);
  color: var(--fg-2);
  font-weight: var(--fw-semibold);
}
</style>
