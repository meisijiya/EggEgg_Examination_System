/**
 * 路由配置 + 鉴权守卫。
 *
 * 路由：
 *  - /login               单密码登录
 *  - /                    首页（最近成绩 + 开始模拟考）
 *  - /exam/:id/intro      考试介绍
 *  - /exam/:id/play       答题
 *  - /exam/:id/result     成绩
 *  - /dashboard           历次趋势 + 雷达
 *  - /admin               题目 review（admin 鉴权）
 *
 * 守卫：
 *  - 未登录访问任何页面（除 /login）→ 跳 /login
 *  - 已登录访问 /login → 跳 /
 *  - /admin 需要 admin role → 跳 /
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    name: 'home',
    component: () => import('@/pages/Home.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/exam/:id/intro',
    name: 'exam-intro',
    component: () => import('@/pages/ExamIntro.vue'),
    meta: { title: '考试介绍' },
  },
  {
    path: '/exam/:id/play',
    name: 'exam-play',
    component: () => import('@/pages/ExamPlay.vue'),
    meta: { title: '答题中' },
  },
  {
    path: '/exam/:id/result',
    name: 'exam-result',
    component: () => import('@/pages/ExamResult.vue'),
    meta: { title: '成绩' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/pages/Dashboard.vue'),
    meta: { title: '仪表盘' },
  },
  {
    path: '/admin',
    name: 'admin',
    component: () => import('@/pages/Admin.vue'),
    meta: { title: '管理员', adminOnly: true },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

/**
 * 全局鉴权守卫。
 */
router.beforeEach((to) => {
  const auth = useAuthStore();

  if (to.meta.title) {
    document.title = `${to.meta.title} · 财务管理考试系统`;
  }

  // 公开路由（/login）
  if (to.meta.public) {
    if (auth.isAuthenticated && to.name === 'login') {
      return { path: '/' };
    }
    return true;
  }

  // 其它路由必须登录
  if (!auth.isAuthenticated) {
    return { path: '/login' };
  }

  // admin-only 路由
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { path: '/' };
  }

  return true;
});
