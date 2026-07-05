<script setup lang="ts">
/**
 * 根组件 — 渲染 <router-view> + 顶部导航。
 *
 * 顶部导航：根据路由 meta 决定是否显示（login / play 不显示，避免干扰）。
 * 视觉：appframe.topnav — 56px 高度，logo 用 sky→sunrise 渐变方块。
 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { useExamStore } from '@/stores/exam';
import SubjectSwitcher from '@/components/SubjectSwitcher.vue';
import type { Subject } from '@/types/api';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const exam = useExamStore();

/** 是否显示顶部导航（考试中、登录页不显示） */
const showNav = computed(() => {
  return (
    route.name !== 'login' &&
    route.name !== 'exam-play'
  );
});

function goHome(): void {
  router.push('/');
}

function goDashboard(): void {
  router.push('/dashboard');
}

function goAdmin(): void {
  router.push('/admin');
}

function handleLogout(): void {
  auth.logout();
  router.push('/login');
}

/**
 * 切换科目 — 写 store + 跳首页(若当前在 dashboard / admin 不强制跳,只换 context)。
 *
 * fix-30a 用户硬约束:切换即生效,所以无脑 push('/'),让 dashboard / result 历史
 * 自然按新 subject 过滤(后端 dashboard 后续按 subject_id 聚合)。
 */
function onSubjectChange(sub: Subject): void {
  exam.setSubject(sub);
  if (route.name !== 'home') {
    router.push('/');
  }
}
</script>

<template>
  <header v-if="showNav" class="topnav">
    <div class="topnav-inner">
      <div class="logo" @click="goHome">
        <span class="logo-ic"></span>
        <span class="logo-text">答题系统</span>
      </div>
      <SubjectSwitcher
        :model-value="exam.currentSubject"
        @update:model-value="onSubjectChange"
      />
      <nav class="topnav-nav">
        <a
          class="topnav-link"
          :class="{ active: route.name === 'home' }"
          @click="goHome"
        >
          首页
        </a>
        <a
          class="topnav-link"
          :class="{ active: route.name === 'dashboard' }"
          @click="goDashboard"
        >
          仪表盘
        </a>
        <a
          v-if="auth.isAdmin"
          class="topnav-link"
          :class="{ active: route.name === 'admin' }"
          @click="goAdmin"
        >
          管理员
        </a>
        <a class="topnav-link logout" @click="handleLogout">退出</a>
      </nav>
    </div>
  </header>

  <main class="app-main">
    <router-view />
  </main>
</template>

<style scoped>
/* ---- 顶部导航（appframe.topnav） ---- */
.topnav {
  height: 56px;
  background: oklch(100% 0 0 / 0.85);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: 0;
  z-index: 100;
}
.topnav-inner {
  max-width: 1200px;
  height: 100%;
  margin: 0 auto;
  padding: 0 var(--s-6);
  display: flex;
  align-items: center;
  gap: var(--s-6);
}
.logo {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  cursor: pointer;
  font: 700 16px / 1 var(--font-display);
  color: var(--sky-active);
  user-select: none;
}
.logo-ic {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--sky), var(--sunrise));
  box-shadow: var(--shadow-xs);
}
.logo-text { letter-spacing: 0.02em; }

.topnav-nav {
  display: flex;
  gap: var(--s-1);
  margin-left: var(--s-6);
}
.topnav-link {
  padding: 6px var(--s-3);
  border-radius: var(--r-sm);
  color: var(--muted);
  font-size: var(--fs-body);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out);
  user-select: none;
}
.topnav-link:hover { background: var(--surface-2); color: var(--fg); }
.topnav-link.active {
  background: var(--sky-soft);
  color: var(--sky-active);
  font-weight: var(--fw-medium);
}
.topnav-link.logout { color: var(--muted-2); }
.topnav-link.logout:hover { background: var(--danger-soft); color: var(--danger); }

/* ---- 主体 ---- */
.app-main {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
}

@media (max-width: 720px) {
  .topnav-inner { padding: 0 var(--s-4); gap: var(--s-3); }
  .topnav-nav { margin-left: var(--s-3); gap: 0; }
  .topnav-link { padding: 6px var(--s-2); font-size: var(--fs-caption); }
  .logo-text { display: none; }
}
</style>
