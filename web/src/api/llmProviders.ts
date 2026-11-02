import client from './client'

export interface LLMProvider {
  id: number
  name: string
  provider_type: string
  api_url: string
  model: string
  temperature: number
  timeout: number
  extra_config: Record<string, any>
  is_default: boolean
  is_active: boolean
  is_deleted: boolean
  deleted_at: string | null
  created_at: string
  updated_at: string
}

export interface ProviderListResponse {
  items: LLMProvider[]
  total: number
}

export function getProviders() {
  return client.get<{ code: number; message: string; data: ProviderListResponse }>('/api/v1/llm-providers/')
}

export function getProvider(id: number) {
  return client.get<{ code: number; message: string; data: LLMProvider }>(`/api/v1/llm-providers/${id}`)
}

export function getDefaultProvider() {
  return client.get<{ code: number; message: string; data: LLMProvider }>('/api/v1/llm-providers/default')
}

export function createProvider(data: {
  name: string
  provider_type: string
  api_url: string
  api_key: string
  model: string
  temperature?: number
  timeout?: number
  extra_config?: Record<string, any>
  is_active?: boolean
}) {
  return client.post<{ code: number; message: string; data: LLMProvider }>('/api/v1/llm-providers/', data)
}

export function updateProvider(id: number, data: {
  name?: string
  provider_type?: string
  api_url?: string
  api_key?: string
  model?: string
  temperature?: number
  timeout?: number
  extra_config?: Record<string, any>
  is_active?: boolean
}) {
  return client.put<{ code: number; message: string; data: LLMProvider }>(`/api/v1/llm-providers/${id}`, data)
}

export function deleteProvider(id: number) {
  return client.delete<{ code: number; message: string }>(`/api/v1/llm-providers/${id}`)
}

export function setDefaultProvider(id: number) {
  return client.put<{ code: number; message: string; data: LLMProvider }>(`/api/v1/llm-providers/${id}/set-default`)
}

export const llmProvidersApi = {
  getProviders,
  getProvider,
  getDefaultProvider,
  createProvider,
  updateProvider,
  deleteProvider,
  setDefaultProvider,
}
