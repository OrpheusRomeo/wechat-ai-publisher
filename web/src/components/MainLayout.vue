<template>
  <el-container class="layout-container">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <div class="logo-icon">🤖</div>
        <h2>AI 发布系统</h2>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="transparent"
        text-color="#bfcbd9"
        active-text-color="#fff"
        :ellipsis="false"
      >
        <el-menu-item index="/dashboard">
          <el-icon><HomeFilled /></el-icon>
          <span>首页</span>
        </el-menu-item>

        <el-sub-menu index="config">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统配置</span>
          </template>
          <el-menu-item index="/config/wechat">微信配置</el-menu-item>
          <el-menu-item index="/config/deepseek">DeepSeek 配置</el-menu-item>
          <el-menu-item index="/config/llm-providers">LLM 模型配置</el-menu-item>
          <el-menu-item index="/config/scheduler">定时任务</el-menu-item>
        </el-sub-menu>

        <el-menu-item index="/ip-whitelist">
          <el-icon><Lock /></el-icon>
          <span>访问控制</span>
        </el-menu-item>

        <el-menu-item index="/platform-ip-whitelist">
          <el-icon><Connection /></el-icon>
          <span>平台 IP 白名单</span>
        </el-menu-item>

        <el-menu-item index="/platform-accounts">
          <el-icon><Connection /></el-icon>
          <span>平台账号</span>
        </el-menu-item>

        <el-sub-menu index="articles">
          <template #title>
            <el-icon><Document /></el-icon>
            <span>文章管理</span>
          </template>
          <el-menu-item index="/articles/hot-report">热搜早报</el-menu-item>
          <el-menu-item index="/articles/keyword">深度文章</el-menu-item>
          <el-menu-item index="/articles/toutiao">今日头条</el-menu-item>
        </el-sub-menu>

        <el-menu-item v-if="userStore.isSuperadmin" index="/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>

        <el-menu-item index="/profile">
          <el-icon><UserFilled /></el-icon>
          <span>个人设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-breadcrumb">
          <span class="page-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <el-dropdown trigger="click">
            <span class="user-info">
              <el-avatar :size="32" class="user-avatar">
                {{ userStore.nickname?.[0] || userStore.username?.[0] || 'U' }}
              </el-avatar>
              <span class="user-name">{{ userStore.nickname || userStore.username }}</span>
              <el-icon class="arrow-icon"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="$router.push('/profile')">
                  <el-icon><UserFilled /></el-icon>
                  个人设置
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import { SwitchButton } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)

const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    '/dashboard': '首页',
    '/config/wechat': '微信配置',
    '/config/deepseek': 'DeepSeek 配置',
    '/config/scheduler': '定时任务',
    '/ip-whitelist': '访问控制',
    '/platform-ip-whitelist': '平台 IP 白名单',
    '/platform-accounts': '平台账号',
    '/articles/hot-report': '热搜早报',
    '/articles/keyword': '深度文章',
    '/articles/toutiao': '今日头条',
    '/users': '用户管理',
    '/profile': '个人设置',
  }
  return titles[route.path] || 'AI 发布系统'
})

function handleLogout() {
  userStore.logout()
  ElMessage.success('已退出登录')
  router.push('/login')
}
</script>

<style scoped>
.layout-container {
  height: 100vh;
}

.aside {
  background: linear-gradient(180deg, #304156 0%, #1f2d3d 100%);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.logo {
  padding: 20px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
}

.logo-icon {
  font-size: 28px;
  line-height: 1;
}

.logo h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.5px;
}

.header {
  background: var(--color-bg-header);
  border-bottom: 1px solid var(--color-border-lighter);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: var(--shadow-sm);
}

.header-breadcrumb {
  display: flex;
  align-items: center;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: var(--radius-md);
  transition: background-color var(--transition-fast);
}

.user-info:hover {
  background-color: var(--color-bg-page);
}

.user-avatar {
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
}

.user-name {
  font-size: 14px;
  color: var(--color-text-primary);
  font-weight: 500;
}

.arrow-icon {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.main-content {
  background: var(--color-bg-page);
  padding: 24px;
}

/* 菜单样式优化 */
:deep(.el-menu) {
  background-color: transparent !important;
  border: none;
}

:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  height: 50px;
  line-height: 50px;
  margin: 2px 8px;
  border-radius: var(--radius-md);
  padding-left: 16px !important;
  padding-right: 16px !important;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background-color: rgba(255, 255, 255, 0.08) !important;
}

:deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, var(--color-primary) 0%, rgba(64, 158, 255, 0.7) 100%) !important;
}

:deep(.el-sub-menu .el-menu-item) {
  height: 44px;
  line-height: 44px;
  margin: 2px 8px;
}

:deep(.el-sub-menu__icon-arrow) {
  color: #bfcbd9;
}
</style>
