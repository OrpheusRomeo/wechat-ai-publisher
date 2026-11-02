import client from './client'

export interface PlatformIPRecord {
  id: number
  platform_type: string
  ip_address: string
  label: string
  is_active: boolean
  is_auto_synced: boolean
  created_at?: string
  updated_at?: string
}

export interface PlatformSummary {
  platform_type: string
  display_name: string
  ip_count: number
}

export interface DetectResult {
  ip_address: string
  is_new: boolean
  record: PlatformIPRecord | null
}

export function getPlatforms() {
  return client.get('/api/v1/platform-ip-whitelist/platforms')
}

export function getList(platform_type?: string) {
  return client.get('/api/v1/platform-ip-whitelist', {
    params: platform_type ? { platform_type } : {},
  })
}

export function create(data: {
  platform_type: string
  ip_address: string
  label?: string
  is_active?: boolean
}) {
  return client.post('/api/v1/platform-ip-whitelist', data)
}

export function update(id: number, data: {
  ip_address?: string
  label?: string
  is_active?: boolean
}) {
  return client.put(`/api/v1/platform-ip-whitelist/${id}`, data)
}

export function remove(id: number) {
  return client.delete(`/api/v1/platform-ip-whitelist/${id}`)
}

export function detectMyIp() {
  return client.post('/api/v1/platform-ip-whitelist/detect-my-ip')
}
