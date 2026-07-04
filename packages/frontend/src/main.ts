/**
 * 应用入口 — Vue + Pinia + Router + Element Plus 装配。
 */
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';
// 设计系统 3 层 — 顺序：令牌 → EP 主题覆盖 → 业务全局
// 1. 设计令牌（OKLch）：颜色 / 字号 / 圆角 / 阴影 / 缓动
import './styles/tokens.css';
// 2. Element Plus 运行时主题（hex，对齐 SCSS 编译时变量）
import './styles/element-overrides.css';
// 3. 业务全局：reset + 排版 + qcard / option / btn / timer / progress / stat
import './styles/global.css';

import App from './App.vue';
import { router } from './router';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: zhCn });
app.mount('#app');
