import client from './client'

// ============ Platform APIs ============

export interface Platform {
  id: number
  platform_type: string
  display_name: string
  icon: string
  is_active: boolean
  created_at: string
}

export interface AccountCredentials {
  app_id?: string
  app_secret?: string
  token?: string
  encoding_aes_key?: string
  client_key?: string
  client_secret?: string
  device_id?: string
  xhs_token?: string
  bduss?: string
  ptoken?: string
  api_key?: string
  channel_id?: string
  default_cover_url?: string
}

export interface PlatformAccount {
  id: number
  account_id: string
  platform_id: number
  account_name: string
  platform_type: string
  credentials_masked: Record<string, string>
  crawl_cron: string
  push_cron: string
  config: Record<string, any>
  is_deleted: boolean
  deleted_at: string | null
  is_active: boolean
  created_at: string
  updated_at: string | null
}

// 与后端 ListResponse[Platform] / PlatformAccountListResponse 对齐
export interface ListResponse<T> {
  code: number
  message: string
  data: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface AccountListResponse {
  items: PlatformAccount[]
  total: number
}

// ============ Platform APIs ============

export function getPlatforms() {
  // 后端返回 ListResponse[PlatformResponse]: { code, message, data: Platform[], total, page, ... }
  return client.get<ListResponse<Platform>>('/api/v1/platforms')
}

export function getPlatform(platformType: string) {
  // 后端返回 DataResponse[PlatformResponse]
  return client.get<{ code: number; message: string; data: Platform }>(`/api/v1/platforms/${platformType}`)
}

// ============ Account APIs ============

export function getAccounts(params?: {
  platform_type?: string
  include_deleted?: boolean
  is_active?: boolean
}) {
  // 后端返回 PlatformAccountListResponse: { items: PlatformAccount[], total }
  return client.get<AccountListResponse>('/api/v1/platforms/accounts', { params })
}

export function getAccount(accountId: string) {
  // 后端返回 DataResponse[PlatformAccountResponse]
  return client.get<{ code: number; message: string; data: PlatformAccount }>(`/api/v1/platforms/accounts/${accountId}`)
}

export function createAccount(data: {
  platform_type: string
  account_name: string
  credentials: AccountCredentials
  crawl_cron?: string
  push_cron?: string
  config?: Record<string, any>
}) {
  return client.post<{ code: number; message: string; data: PlatformAccount }>('/api/v1/platforms/accounts', data)
}

export function updateAccount(accountId: string, data: {
  account_name?: string
  credentials?: AccountCredentials
  crawl_cron?: string
  push_cron?: string
  config?: Record<string, any>
  is_active?: boolean
}) {
  return client.put<{ code: number; message: string; data: PlatformAccount }>(`/api/v1/platforms/accounts/${accountId}`, data)
}

export function softDeleteAccount(accountId: string) {
  return client.delete<{ code: number; message: string }>(`/api/v1/platforms/accounts/${accountId}`)
}

export function restoreAccount(accountId: string) {
  return client.post<{ code: number; message: string }>(`/api/v1/platforms/accounts/${accountId}/restore`)
}

export function permanentDeleteAccount(accountId: string) {
  return client.delete<{ code: number; message: string }>(`/api/v1/platforms/accounts/${accountId}/permanent`)
}

export function updateAccountSchedule(accountId: string, data: {
  crawl_cron?: string
  push_cron?: string
}) {
  return client.patch<{ code: number; message: string; data: PlatformAccount }>(`/api/v1/platforms/accounts/${accountId}/schedule`, data)
}

export function getAccountTopics(accountId: string, params?: { status?: string; page?: number; page_size?: number }) {
  // 后端返回 ListResponse: { code, message, data: Topic[], total, page, page_size, ... }
  return client.get<ListResponse<any>>(`/api/v1/platforms/accounts/${accountId}/topics`, { params })
}

export function getAccountArticles(accountId: string, params?: { status?: string; page?: number; page_size?: number }) {
  // 后端返回 ListResponse: { code, message, data: Article[], total, page, page_size, ... }
  return client.get<ListResponse<any>>(`/api/v1/platforms/accounts/${accountId}/articles`, { params })
}

// ============ 账号 API 统一命名空间（供 views 使用）============

// 注意: getAccounts / getAccountTopics / getAccountArticles 返回值结构如下,
// 与后端 ListResponse 对齐: { code, message, data: T[], total, page, page_size, total_pages }
export const platformAccountsApi = {
  getAccounts,
  getAccount,
  createAccount,
  updateAccount,
  softDeleteAccount,
  restoreAccount,
  permanentDeleteAccount,
  updateAccountSchedule,
  getAccountTopics,
  getAccountArticles,
}

// ============ Platform Types ============

export const PLATFORM_TYPES = [
  { value: 'wechat', label: '微信公众号', icon: 'wechat' },
  { value: 'douyin', label: '抖音', icon: 'video' },
  { value: 'xiaohongshu', label: '小红书', icon: 'star' },
  { value: 'baijiahao', label: '百度百家号', icon: 'document' },
  { value: 'toutiao', label: '头条', icon: 'reading' },
  { value: 'youtube', label: 'YouTube', icon: 'video-play' },
] as const

export type PlatformType = typeof PLATFORM_TYPES[number]['value']
