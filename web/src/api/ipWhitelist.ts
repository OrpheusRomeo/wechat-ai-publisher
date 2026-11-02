import client from './client'

export const ipWhitelistApi = {
  async getAll(enabledOnly = false) {
    const res = await client.get('/api/v1/ip-whitelist', { params: { enabled_only: enabledOnly } })
    return res.data.data
  },
  async create(data: { ip_or_cidr: string; label: string; enabled: boolean }) {
    const res = await client.post('/api/v1/ip-whitelist', data)
    return res.data.data
  },
  async update(id: number, data: any) {
    const res = await client.put(`/api/v1/ip-whitelist/${id}`, data)
    return res.data.data
  },
  async delete(id: number) {
    const res = await client.delete(`/api/v1/ip-whitelist/${id}`)
    return res.data
  },
  async check(ip: string) {
    const res = await client.post(`/api/v1/ip-whitelist/check?ip=${encodeURIComponent(ip)}`)
    return res.data.data
  },
}
