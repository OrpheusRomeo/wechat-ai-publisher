import client from './client'

export const keywordArticlesApi = {
  async create(keyword: string, style = 'standard', autoPublish = false, customPrompt: string = '') {
    const res = await client.post('/api/v1/keyword-articles', {
      keyword,
      style,
      auto_publish: autoPublish,
      custom_prompt: customPrompt,
    })
    // 返回完整的 DataResponse，包含 task_id
    return res.data
  },
  async getAll(params?: { status?: string; page?: number; page_size?: number }) {
    const res = await client.get('/api/v1/keyword-articles', { params })
    return res.data
  },
  async getOne(id: number) {
    const res = await client.get(`/api/v1/keyword-articles/${id}`)
    return res.data.data
  },
  async batchDelete(ids: number[]) {
    const res = await client.post('/api/v1/keyword-articles/batch_delete', { ids })
    return res.data
  },
}
