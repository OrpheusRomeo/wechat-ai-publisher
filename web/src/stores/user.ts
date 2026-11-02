import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'
import { secondsUntilExp } from '@/utils/jwt'

// 距过期多久提前刷新 (秒). access_token 默认 60 分钟, 提前 5 分钟刷新.
const REFRESH_AHEAD_SECONDS = 5 * 60
// 切回前台时, 剩余时间低于这个阈值就立即刷 (秒)
const VISIBILITY_REFRESH_THRESHOLD = 60
// silent refresh 失败/refresh_token 过期 后, 冷却多久允许再次尝试 (秒)
const REFRESH_RETRY_COOLDOWN = 30

let refreshTimer: number | null = null
let refreshCooldownUntil = 0

// 安全注意: tokens 存储在 localStorage 中存在 XSS 风险.
// 如需更高安全性, 应使用 HttpOnly cookie 存储 refresh_token,
// 并通过后端 API 续期access_token. 当前实现适合内部管理后台场景.
export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem('access_token') || '')
  const refreshToken = ref<string>(localStorage.getItem('refresh_token') || '')
  const username = ref<string>(localStorage.getItem('username') || '')
  const role = ref<string>(localStorage.getItem('role') || '')
  const nickname = ref<string>(localStorage.getItem('nickname') || '')

  const isLoggedIn = computed(() => !!token.value)
  const isSuperadmin = computed(() => role.value === 'superadmin')

  function _persist() {
    localStorage.setItem('access_token', token.value)
    localStorage.setItem('refresh_token', refreshToken.value)
    localStorage.setItem('username', username.value)
    localStorage.setItem('role', role.value)
    localStorage.setItem('nickname', nickname.value)
  }

  async function login(user: string, pass: string) {
    const res = await authApi.login(user, pass)
    token.value = res.access_token
    refreshToken.value = res.refresh_token
    _persist()
    // 获取用户信息
    try {
      const me = await authApi.getMe()
      username.value = me.username
      role.value = me.role
      nickname.value = me.nickname
      _persist()
    } catch { /* ignore */ }
    scheduleSilentRefresh()
  }

  /**
   * 静默刷新 access_token. 用于客户端拦截器 401 时的自动续期,
   * 也用于定时器提前刷新. 返回 ok / error 让调用方决定下一步.
   */
  async function refreshAccessToken(): Promise<{ ok: boolean; error?: string }> {
    if (!refreshToken.value) {
      return { ok: false, error: 'no refresh token' }
    }
    try {
      const res = await authApi.refresh(refreshToken.value)
      token.value = res.access_token
      refreshToken.value = res.refresh_token
      _persist()
      scheduleSilentRefresh()
      return { ok: true }
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || 'refresh failed'
      // 进入冷却, 防止短期内反复请求 refresh 接口
      refreshCooldownUntil = Date.now() + REFRESH_RETRY_COOLDOWN * 1000
      return { ok: false, error: detail }
    }
  }

  function logout() {
    clearSilentRefresh()
    token.value = ''
    refreshToken.value = ''
    username.value = ''
    role.value = ''
    nickname.value = ''
    localStorage.clear()
  }

  /**
   * 安排一次 silent refresh. 在 token 过期前 REFRESH_AHEAD_SECONDS
   * 秒触发, 自动续期. 每次调用都会清掉旧的 timer 再排新的.
   */
  function scheduleSilentRefresh() {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
    if (!token.value) return
    const remaining = secondsUntilExp(token.value)
    if (remaining === -Infinity) return  // 解析失败, 不安排
    const delay = (remaining - REFRESH_AHEAD_SECONDS) * 1000
    // 如果已经过期或即将过期 (< REFRESH_AHEAD_SECONDS), 安排在 1 秒后
    const finalDelay = delay > 0 ? delay : 1000
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null
      // 冷却中跳过 (上次失败过)
      if (Date.now() < refreshCooldownUntil) {
        scheduleSilentRefresh()
        return
      }
      refreshAccessToken().then((r) => {
        if (!r.ok) {
          // refresh_token 也过期了, 强制登出
          logout()
          // 路由跳转交给拦截器或 Login.vue 处理, 这里只清状态
          window.location.hash = '#/login'
        }
      })
    }, finalDelay)
  }

  function clearSilentRefresh() {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  /**
   * 启动 silent refresh + visibilitychange 监听. 必须在 pinia 装好之后
   * (app.use(pinia) 之后) 调用一次. 由 main.ts 触发.
   */
  function setupAutoRefresh() {
    scheduleSilentRefresh()
    document.addEventListener('visibilitychange', () => {
      // 切回前台, 且 access_token 即将过期 → 立即刷新
      if (document.visibilityState === 'visible' && token.value) {
        const remaining = secondsUntilExp(token.value)
        if (remaining !== -Infinity && remaining < VISIBILITY_REFRESH_THRESHOLD) {
          if (Date.now() >= refreshCooldownUntil) {
            refreshAccessToken()
          }
        }
      }
    })
  }

  return {
    token,
    refreshToken,
    username,
    role,
    nickname,
    isLoggedIn,
    isSuperadmin,
    login,
    refreshAccessToken,
    logout,
    scheduleSilentRefresh,
    clearSilentRefresh,
    setupAutoRefresh,
  }
})