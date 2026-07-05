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
import { ElMessage, ElMessageBox } from 'element-plus';
import { getReviewQueue, updateQuestion } from '@/api';
import {
  getAiGeneratedQuestions,
  approveQuestion,
  rejectQuestion,
} from '@/api/subjects';
import { useAuthStore } from '@/stores/auth';
import { renderMarkdown } from '@/composables/useMarkdown';
import type {
  ReviewQueueItem,
  ReviewUpdateRequest,
  AiGeneratedQuestion,
} from '@/types/api';

const auth = useAuthStore();

const queue = ref<ReviewQueueItem[]>([]);
const loading = ref<boolean>(false);
const password = ref<string>('');
const loggingIn = ref<boolean>(false);
const editing = ref<ReviewQueueItem | null>(null);
const editDialogVisible = ref<boolean>(false);
const editForm = ref<ReviewUpdateRequest>({});
const saving = ref<boolean>(false);

/** 当前激活的 tab:'review' = 原题目 review,'ai-generated' = fix-30a AI 出题审核。 */
const activeTab = ref<'review' | 'ai-generated'>('review');

/** fix-30a:AI 生成题 review queue 状态。 */
const aiQuestions = ref<AiGeneratedQuestion[]>([]);
const aiLoading = ref<boolean>(false);
/** 用户硬约束:source_ref 折叠栏默认收起(逐条 id 控制展开态)。 */
const expandedAiRows = ref<number[]>([]);

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

/**
 * fix-30a:加载 AI 生成题 review queue。
 */
async function loadAiQuestions(): Promise<void> {
  aiLoading.value = true;
  try {
    const r = await getAiGeneratedQuestions();
    aiQuestions.value = r.items;
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '加载 AI 题失败';
    ElMessage.error(msg);
  } finally {
    aiLoading.value = false;
  }
}

/**
 * fix-30a:批准一条 AI 生成题。
 */
async function onApproveAi(qid: number): Promise<void> {
  try {
    await approveQuestion(qid);
    ElMessage.success(`已批准题目 #${qid}`);
    aiQuestions.value = aiQuestions.value.filter((q) => q.id !== qid);
  } catch (e) {
    ElMessage.error((e as { message?: string })?.message ?? '批准失败');
  }
}

/**
 * fix-30a:拒绝一条 AI 生成题 — 弹窗输入原因。
 */
async function onRejectAi(qid: number): Promise<void> {
  try {
    const { value: reason } = await ElMessageBox.prompt(
      '请输入拒绝原因（学员可见，用于复盘）',
      `拒绝题目 #${qid}`,
      {
        confirmButtonText: '确认拒绝',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例：关键要点缺失；引用资料错位；题目重复...',
        inputValidator: (val: string) => {
          if (!val || val.trim().length < 4) return '请输入至少 4 个字符';
          return true;
        },
      },
    );
    await rejectQuestion(qid, reason.trim());
    ElMessage.success(`已拒绝题目 #${qid}`);
    aiQuestions.value = aiQuestions.value.filter((q) => q.id !== qid);
  } catch (e) {
    // 用户取消 = 静默;网络错误 = 提示
    if ((e as { message?: string })?.message) {
      ElMessage.error((e as { message?: string }).message ?? '拒绝失败');
    }
  }
}

/**
 * fix-30a:切换 tab 时按需懒加载。
 */
function onTabChange(name: string | number | undefined): void {
  const tab = String(name);
  if (tab === 'ai-generated' && aiQuestions.value.length === 0 && !aiLoading.value) {
    loadAiQuestions();
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

/**
 * 测试钩子 — 暴露内部状态给 vitest,避免依赖 DOM 触发 tab-change 事件。
 * 生产代码不依赖。
 */
defineExpose({
  activeTab,
  aiQuestions,
  expandedAiRows,
  onApproveAi,
  onRejectAi,
  onTabChange,
  loadAiQuestions,
});
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

    <!-- 已登录：review queue + AI 生成题审核（fix-30a tabs） -->
    <div v-else>
      <div class="admin-header">
        <h1>🛠️ 题目 Review</h1>
        <div class="admin-actions">
          <el-button type="danger" plain @click="handleLogout">退出</el-button>
        </div>
      </div>

      <el-tabs v-model="activeTab" class="admin-tabs" @tab-change="onTabChange">
        <!-- Tab 1：原题目 review -->
        <el-tab-pane label="原题库 review" name="review">
          <div class="tab-actions">
            <el-tag type="warning" size="large">
              待 review: {{ queue.length }} 题
            </el-tag>
            <el-button @click="loadQueue" :loading="loading">🔄 刷新</el-button>
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
                <div class="stem-cell" v-html="renderMarkdown(row.stem)" />
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
        </el-tab-pane>

        <!-- Tab 2：AI 生成题审核（fix-30a） -->
        <el-tab-pane label="AI 生成题 review" name="ai-generated">
          <div class="tab-actions">
            <el-tag type="info" size="large">
              待审核: {{ aiQuestions.length }} 题
            </el-tag>
            <el-button @click="loadAiQuestions" :loading="aiLoading">🔄 刷新</el-button>
          </div>

          <div v-if="aiQuestions.length === 0 && !aiLoading" class="empty-state">
            🎉 暂无 AI 待审核题目
          </div>

          <el-table
            v-else
            :data="aiQuestions"
            stripe
            :expand-row-keys="expandedAiRows"
            row-key="id"
            @expand-change="(rows: AiGeneratedQuestion[]) => (expandedAiRows = rows.map((r) => r.id))"
          >
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="ai-expand">
                  <h4>📎 来源资料引用</h4>
                  <p class="src-meta">
                    <strong>{{ row.source_ref.file }}</strong> ·
                    第 {{ row.source_ref.paragraph_index }} 段
                  </p>
                  <blockquote class="src-snippet">
                    {{ row.source_ref.snippet }}
                  </blockquote>
                  <template v-if="row.agent_trace?.length">
                    <h4>🧠 Agent Pipeline Trace</h4>
                    <ul class="agent-trace">
                      <li v-for="(t, i) in row.agent_trace" :key="i">
                        <strong>{{ t.agent }}:</strong> {{ t.output }}
                      </li>
                    </ul>
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="subject_id" label="科目" width="120" />
            <el-table-column prop="type" label="题型" width="100" />
            <el-table-column prop="chapter_code" label="章节" width="100" />
            <el-table-column prop="difficulty" label="难度" width="80" />
            <el-table-column label="置信度" width="100">
              <template #default="{ row }">
                <el-progress
                  :percentage="Math.round(row.confidence * 100)"
                  :stroke-width="10"
                  :color="row.confidence >= 0.8 ? 'var(--success)' : row.confidence >= 0.6 ? 'var(--warning)' : 'var(--danger)'"
                />
              </template>
            </el-table-column>
            <el-table-column label="题干">
              <template #default="{ row }">
                <div class="stem-cell" v-html="renderMarkdown(row.stem)" />
              </template>
            </el-table-column>
            <el-table-column label="AI 答案" width="120">
              <template #default="{ row }">
                <div class="answer-cell">{{ row.generated_answer }}</div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  type="success"
                  data-testid="ai-approve-btn"
                  @click="onApproveAi(row.id)"
                >
                  ✓ 批准
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  data-testid="ai-reject-btn"
                  @click="onRejectAi(row.id)"
                >
                  ✗ 拒绝
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
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
          <div class="readonly-stem" v-html="renderMarkdown(editing.stem)" />
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

/* fix-30: admin stem-cell 内 GFM 表格最小样式 (与 QuestionCard 对齐) */
.stem-cell :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: var(--fs-body);
  font-weight: 400;
  margin: var(--s-1) 0;
}
.stem-cell :deep(th),
.stem-cell :deep(td) {
  border: 1px solid var(--border);
  padding: var(--s-1) var(--s-2);
  text-align: left;
}
.stem-cell :deep(th) {
  background: var(--surface-2);
}

.readonly-stem {
  background: var(--surface-2);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  white-space: pre-wrap;
  max-height: 240px;
  overflow-y: auto;
  color: var(--fg);
  border-left: 3px solid var(--border-strong);
}

/* fix-30: readonly-stem 内 GFM 表格 (admin edit dialog) */
.readonly-stem :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: var(--fs-body);
  font-weight: 400;
  margin: var(--s-2) 0;
}
.readonly-stem :deep(th),
.readonly-stem :deep(td) {
  border: 1px solid var(--border);
  padding: var(--s-1) var(--s-3);
  text-align: left;
}
.readonly-stem :deep(th) {
  background: var(--surface);
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

/* fix-30a：admin tabs + AI 生成题 review 样式 */
.admin-tabs {
  background: var(--surface);
  border-radius: var(--r-lg);
  padding: var(--s-4) var(--s-5);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-xs);
}
.admin-tabs :deep(.el-tabs__header) {
  margin-bottom: var(--s-4);
}
.admin-tabs :deep(.el-tabs__item) {
  font: var(--fw-medium) var(--fs-body-lg) / 1 var(--font-display);
}
.admin-tabs :deep(.el-tabs__item.is-active) {
  color: var(--sky-active);
}
.tab-actions {
  display: flex;
  gap: var(--s-3);
  align-items: center;
  margin-bottom: var(--s-3);
}
.ai-expand {
  background: var(--surface-2);
  padding: var(--s-4) var(--s-5);
  border-radius: var(--r-md);
  margin: var(--s-2) 0;
}
.ai-expand h4 {
  margin: var(--s-3) 0 var(--s-2);
  font: var(--fw-semibold) var(--fs-body) / 1 var(--font-display);
  color: var(--fg);
}
.ai-expand h4:first-child {
  margin-top: 0;
}
.src-meta {
  font-size: var(--fs-caption);
  color: var(--muted);
  margin: 0 0 var(--s-2);
}
.src-snippet {
  margin: 0;
  padding: var(--s-3) var(--s-4);
  background: var(--surface);
  border-left: 3px solid var(--sky);
  border-radius: var(--r-sm);
  font: var(--fw-regular) var(--fs-body) / 1.6 var(--font-display);
  color: var(--fg-2);
  white-space: pre-wrap;
}
.agent-trace {
  margin: 0;
  padding-left: var(--s-5);
  font-size: var(--fs-body);
  color: var(--fg-2);
}
.agent-trace li {
  margin-bottom: var(--s-2);
}
.answer-cell {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--fg-2);
}
</style>
