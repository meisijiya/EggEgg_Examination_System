<script setup lang="ts">
/**
 * /login 页面 — 单密码登录。
 *
 * - 表单：密码输入框
 * - 提交：调 /auth/login，成功后持久化到 localStorage + 跳 /
 * - 错误：密码错误 / 网络错误提示
 * - URL ?reason=expired:由 axios 401 拦截器跳来,提示"登录已过期"
 */
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const password = ref<string>('');
const submitting = ref<boolean>(false);

onMounted(() => {
  // axios 401 拦截器跳来时会给 ?reason=expired,给用户清晰提示,
  // 避免看到登录页时不知道为什么被踢回来。
  if (route.query.reason === 'expired') {
    ElMessage.warning('登录已过期，请重新登录');
  }
});

async function handleSubmit(): Promise<void> {
  if (!password.value) {
    ElMessage.warning('请输入密码');
    return;
  }
  submitting.value = true;
  try {
    await auth.login(password.value);
    ElMessage.success('登录成功');
    password.value = '';
    router.push('/');
  } catch (err) {
    const msg = (err as { message?: string })?.message ?? '登录失败';
    ElMessage.error(msg);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <template #header>
        <div class="login-title">📚 财务管理考试系统</div>
        <div class="login-subtitle">学员登录</div>
      </template>
      <el-form @submit.prevent="handleSubmit" label-position="top">
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            placeholder="请输入学员密码"
            show-password
            size="large"
            autocomplete="current-password"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          :loading="submitting"
          style="width: 100%"
          @click="handleSubmit"
        >
          登录
        </el-button>
      </el-form>
      <div class="login-hint">
        💡 仅 1 名学员使用，输入密码即可进入
      </div>
    </el-card>
  </div>
</template>

<style scoped>
/* 登录页 — 雪 + 蓝天 + 旭日背景，玻璃卡片 */
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(800px 600px at 80% 10%, oklch(92% 0.08 60 / 0.45), transparent 60%),
    radial-gradient(700px 500px at 10% 90%, oklch(88% 0.08 232 / 0.55), transparent 65%),
    var(--bg);
  padding: var(--s-6) var(--s-4);
}
.login-card {
  width: 100%;
  max-width: 420px;
  border-radius: var(--r-xl) !important;
  border: 1px solid var(--border) !important;
  box-shadow: var(--shadow-lg) !important;
  background: oklch(100% 0 0 / 0.92) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.login-card :deep(.el-card__header) {
  padding: var(--s-6) var(--s-6) 0;
  border-bottom: 0;
}
.login-title {
  font-size: 22px;
  font-weight: 700;
  text-align: center;
  color: var(--fg);
}
.login-subtitle {
  font-size: var(--fs-body);
  color: var(--muted);
  text-align: center;
  margin-top: var(--s-2);
}
.login-hint {
  font-size: var(--fs-body);
  color: var(--muted);
  text-align: center;
  margin-top: var(--s-4);
  padding: var(--s-2) var(--s-3);
  background: var(--sky-soft);
  border-radius: var(--r-sm);
  border-left: 3px solid var(--sky);
}
.login-hint strong { color: var(--sky-active); }
</style>
