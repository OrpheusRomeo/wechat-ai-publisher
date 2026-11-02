/**
 * JWT 解析工具
 *
 * 不引入 jwt-decode 依赖, 直接用 atob 解析 base64url. 仅服务端可控签名
 * 的 token (本地登录拿到的 access_token / refresh_token) 会进到这里,
 * 解析失败时统一返回 null, 不抛异常.
 */

export interface JwtPayload {
  /** subject: 用户 id */
  sub: string
  username: string
  role: string
  /** 过期时间 (unix 秒) */
  exp: number
  /** 签发时间 (unix 秒) */
  iat: number
  type: 'access' | 'refresh'
}

export function decodeJwt(token: string): JwtPayload | null {
  if (!token || typeof token !== 'string') return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    // base64url → base64, atob 不认识 '-_' 必须替换
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    // 补齐 padding: base64 长度必须是 4 的倍数
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const json = atob(padded)
    const payload = JSON.parse(json) as JwtPayload
    return payload
  } catch {
    return null
  }
}

/**
 * 距过期还有多少秒
 * @returns 正数=剩余秒数; 0 或负数=已过期; -Infinity=无法解析
 */
export function secondsUntilExp(token: string): number {
  const p = decodeJwt(token)
  if (!p?.exp) return -Infinity
  return p.exp - Math.floor(Date.now() / 1000)
}