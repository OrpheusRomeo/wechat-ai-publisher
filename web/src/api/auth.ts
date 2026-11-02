import client from './client'

export const authApi = {
  async login(username: string, password: string) {
    const res = await client.post('/api/v1/auth/login', { username, password })
    return res.data.data
  },
  async refresh(refreshToken: string) {
    const res = await client.post('/api/v1/auth/refresh', { refresh_token: refreshToken })
    return res.data.data
  },
  async getMe() {
    const res = await client.get('/api/v1/auth/me')
    return res.data.data
  },
  async changePassword(oldPwd: string, newPwd: string) {
    const res = await client.post('/api/v1/auth/change-password', {
      old_password: oldPwd,
      new_password: newPwd,
    })
    return res.data
  },
  async listUsers() {
    const res = await client.get('/api/v1/auth/users')
    return res.data.data
  },
  async createUser(data: any) {
    const res = await client.post('/api/v1/auth/users', data)
    return res.data.data
  },
  async updateUser(id: number, data: any) {
    const res = await client.put(`/api/v1/auth/users/${id}`, data)
    return res.data.data
  },
  async deleteUser(id: number) {
    const res = await client.delete(`/api/v1/auth/users/${id}`)
    return res.data
  },
}
