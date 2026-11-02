import client from './client'

export const settingsApi = {
  async getAll(category?: string) {
    const res = await client.get('/api/v1/settings', { params: { category } })
    return res.data.data
  },
  async update(key: string, value: string) {
    const res = await client.put(`/api/v1/settings/${key}`, { value })
    return res.data.data
  },
  async batchUpdate(settings: Array<{ key: string; value: string }>) {
    const res = await client.put('/api/v1/settings/batch', { settings })
    return res.data
  },
}
