import { createRouter, createWebHashHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { noAuth: true },
  },
  {
    path: '/',
    component: () => import('@/components/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页' },
      },
      {
        path: 'platform-accounts',
        name: 'PlatformAccounts',
        component: () => import('@/views/PlatformAccounts.vue'),
        meta: { title: '平台账号' },
      },
      {
        path: 'config/wechat',
        name: 'WeChatConfig',
        component: () => import('@/views/WeChatConfig.vue'),
        meta: { title: '微信配置' },
      },
      {
        path: 'config/deepseek',
        name: 'DeepSeekConfig',
        component: () => import('@/views/DeepSeekConfig.vue'),
        meta: { title: 'DeepSeek 配置' },
      },
      {
        path: 'config/llm-providers',
        name: 'LLMProviders',
        component: () => import('@/views/LLMProviders.vue'),
        meta: { title: 'LLM 模型配置' },
      },
      {
        path: 'config/scheduler',
        name: 'SchedulerConfig',
        component: () => import('@/views/SchedulerConfig.vue'),
        meta: { title: '定时任务' },
      },
      {
        path: 'ip-whitelist',
        name: 'IPWhitelist',
        component: () => import('@/views/IPWhitelist.vue'),
        meta: { title: '访问控制白名单' },
      },
      {
        path: 'platform-ip-whitelist',
        name: 'PlatformIPWhitelist',
        component: () => import('@/views/PlatformIpWhitelist.vue'),
        meta: { title: '平台 IP 白名单' },
      },
      {
        path: 'users',
        name: 'UserManagement',
        component: () => import('@/views/UserManagement.vue'),
        meta: { title: '用户管理', superadmin: true },
      },
      {
        path: 'articles/hot-report',
        name: 'HotReportBuilder',
        component: () => import('@/views/HotReportBuilder.vue'),
        meta: { title: '热搜早报' },
      },
      {
        path: 'articles/keyword',
        name: 'KeywordArticleBuilder',
        component: () => import('@/views/KeywordArticleBuilder.vue'),
        meta: { title: '深度文章' },
      },
      {
        path: 'articles/toutiao',
        name: 'ToutiaoHotTopics',
        component: () => import('@/views/ToutiaoHotTopics.vue'),
        meta: { title: '今日头条' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/Profile.vue'),
        meta: { title: '个人设置' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  const userStore = useUserStore()
  if (!to.meta.noAuth && !userStore.token) {
    next('/login')
  } else if (to.path === '/login' && userStore.token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
