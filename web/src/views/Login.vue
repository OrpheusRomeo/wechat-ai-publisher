<template>
  <div class="login-wrapper">
    <div class="login-background">
      <div class="bg-gradient"></div>
      <div class="bg-decoration">
        <div class="decoration-circle circle-1"></div>
        <div class="decoration-circle circle-2"></div>
        <div class="decoration-circle circle-3"></div>
      </div>
    </div>

    <div class="login-container">
      <div class="login-brand">
        <div class="brand-icon">🤖</div>
        <h1 class="brand-title">微信AI文章发布系统</h1>
        <p class="brand-subtitle">自动化内容创作与发布平台</p>
      </div>

      <el-card class="login-card" shadow="never">
        <h2 class="card-title">管理员登录</h2>
        <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin" size="large">
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              placeholder="请输入用户名"
              prefix-icon="User"
              clearable
              autocomplete="username"
            />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              prefix-icon="Lock"
              show-password
              autocomplete="current-password"
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              class="login-button"
              :loading="loading"
              @click="handleLogin"
            >
              {{ loading ? '登录中...' : '登 录' }}
            </el-button>
          </el-form-item>
        </el-form>
        <div class="login-footer">
          <span class="hint-text">首次登录后请立即修改密码</span>
        </div>
      </el-card>

      <p class="copyright">© 2024 AI Publisher. All rights reserved.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'

const router = useRouter()
const userStore = useUserStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  loading.value = true
  try {
    await userStore.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrapper {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #0f1c2e;
  position: relative;
  overflow: hidden;
}

.login-background {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.bg-gradient {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse at 20% 80%, rgba(64, 158, 255, 0.15) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(103, 194, 58, 0.1) 0%, transparent 50%),
    radial-gradient(ellipse at 50% 50%, rgba(230, 162, 60, 0.05) 0%, transparent 70%);
}

.bg-decoration {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.decoration-circle {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.03);
}

.circle-1 {
  width: 600px;
  height: 600px;
  top: -200px;
  right: -100px;
  animation: float 20s ease-in-out infinite;
}

.circle-2 {
  width: 400px;
  height: 400px;
  bottom: -100px;
  left: -100px;
  animation: float 15s ease-in-out infinite reverse;
}

.circle-3 {
  width: 300px;
  height: 300px;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation: pulse 10s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-30px); }
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: translate(-50%, -50%) scale(1); }
  50% { opacity: 0.5; transform: translate(-50%, -50%) scale(1.1); }
}

.login-container {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
}

.login-brand {
  text-align: center;
  margin-bottom: 32px;
}

.brand-icon {
  font-size: 56px;
  line-height: 1;
  margin-bottom: 16px;
  animation: bounce 2s ease-in-out infinite;
}

@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

.brand-title {
  margin: 0 0 8px 0;
  font-size: 26px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1px;
}

.brand-subtitle {
  margin: 0;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.5);
}

.login-card {
  width: 400px;
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(20px);
}

.card-title {
  margin: 0 0 24px 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text-primary);
  text-align: center;
}

.login-button {
  width: 100%;
  height: 44px;
  font-size: 16px;
  font-weight: 600;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
  border: none;
}

.login-button:hover {
  background: linear-gradient(135deg, var(--color-primary-light) 0%, var(--color-primary) 100%);
}

.login-footer {
  margin-top: 16px;
  text-align: center;
}

.hint-text {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.copyright {
  margin: 24px 0 0 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.3);
}
</style>
