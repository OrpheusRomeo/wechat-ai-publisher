import axios from 'axios'
import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import router from '@/router'

const client = axios.create({
  baseURL: '/',
  timeout: 30000,
})

// ---- 请求拦截器：注入 Bearer token ----
client.interceptors.request.use((config) => {
  const userStore = useUserStore()
  if (userStore.token) {
    config.headers.Authorization = `Bearer ${userStore.token}`
  }
  return config
})

// ---- 响应拦截器：401 自动续期 / 过期跳登录 ----
type FailedRequest = {
  resolve: (token: string) => void
  reject: (err: unknown) => void
}

let isRefreshing = false
let failedQueue: FailedRequest[] = []

function processQueue(error: unknown, token: string | null) {
  const queue = failedQueue
  failedQueue = []
  queue.forEach((p) => {
    if (error) {
      p.reject(error)
    } else {
      p.resolve(token as string)
    }
  })
}

/** 单例跳转保护: 同一会话内多次 401 只跳一次, 不堆叠 router.push */
let redirectingToLogin = false
async function redirectToLogin(reason: 'expired' | 'disabled' | 'unauthenticated') {
  if (redirectingToLogin) return
  redirectingToLogin = true
  const userStore = useUserStore()
  // 已经登出 (localStorage 已清) 不再 toast
  const wasLoggedIn = !!userStore.token || !!localStorage.getItem('access_token')
  userStore.logout()
  if (wasLoggedIn) {
    if (reason === 'disabled') {
      ElMessage.error('账号已被禁用，请联系管理员')
    } else if (reason === 'expired') {
      ElMessage.warning('登录已过期，请重新登录')
    } else {
      ElMessage.warning('请先登录')
    }
  }
  if (router.currentRoute.value.path !== '/login') {
    router.push('/login')
  }
  // 给 toast 一点时间显示, 再重置标志
  setTimeout(() => { redirectingToLogin = false }, 1000)
}

client.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    const status = error.response?.status
    const url = original?.url ?? ''
    const userStore = useUserStore()

    // ── 守卫：refresh 接口本身 401 不重试 (递归防护) ──
    const isRefreshCall = url.includes('/api/v1/auth/refresh')
    const isLoginCall = url.includes('/api/v1/auth/login')

    // ── 路径 A: 401 + 非 refresh/login 调用 + 有 refreshToken ──
    if (
      status === 401 &&
      !isRefreshCall &&
      !isLoginCall &&
      userStore.refreshToken &&
      !original?._retry
    ) {
      original!._retry = true

      if (isRefreshing) {
        // 已经有 refresh 在跑, 把当前请求挂到队列, 拿新 token 后重试
        return new Promise<string>((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        }).then((newToken) => {
          original!.headers.Authorization = `Bearer ${newToken}`
          return client(original!)
        })
      }

      isRefreshing = true
      const result = await userStore.refreshAccessToken()
      isRefreshing = false

      if (result.ok && userStore.token) {
        processQueue(null, userStore.token)
        original!.headers.Authorization = `Bearer ${userStore.token}`
        return client(original!)
      }

      // refresh 失败 → 整批 reject, 踢到登录页
      processQueue(new Error(result.error || 'refresh failed'), null)
      redirectToLogin('expired')
      return Promise.reject(error)
    }

    // ── 路径 B: 401 + 无 refreshToken / 是 refresh/login 调用 ──
    if (status === 401) {
      // login 401 由 Login.vue 自己捕获并显示 "用户名或密码错误", 不在这里再 toast
      if (!isLoginCall) {
        redirectToLogin(userStore.refreshToken ? 'expired' : 'unauthenticated')
      }
      return Promise.reject(error)
    }

    // ── 路径 C: 403 + 已登录 → 用户被禁用 → 强制登出 ──
    if (status === 403 && userStore.token) {
      redirectToLogin('disabled')
      return Promise.reject(error)
    }

    // ── 路径 D: 其他错误的友好提示 ----
    if (status === 403) {
      ElMessage.error('没有访问权限')
    } else if (status === 422) {
      const detail = (error.response?.data as any)?.detail
      const msg = Array.isArray(detail)
        ? detail.map((d: any) => `${d.loc?.join('.')} ${d.msg}`).join(', ')
        : (typeof detail === 'string' ? detail : '请求数据格式不正确')
      ElMessage.error(msg)
      console.warn('422 from', url, error.response?.data)
    } else if (status && status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    } else if (!status) {
      // 网络断开 / 请求被取消: axios 不带 response, 只在 dev 提示, 避免污染 prod 日志
      if (import.meta.env.DEV) {
        console.warn('[network]', url, error.message)
      }
    }

    return Promise.reject(error)
  },
)

export default client