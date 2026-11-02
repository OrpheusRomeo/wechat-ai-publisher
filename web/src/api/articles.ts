import client from './client'

export const articlesApi = {
  async getAll(params?: any) {
    const res = await client.get('/api/v1/articles/', { params })
    return res.data
  },
  async getOne(id: number) {
    const res = await client.get(`/api/v1/articles/${id}`)
    return res.data
  },
  async generate(topicId: number, choiceId?: string, userOpenid?: string, customPrompt?: string) {
    const res = await client.post('/api/v1/articles/generate', {
      topic_id: topicId,
      choice_id: choiceId || '',
      user_openid: userOpenid || '',
      custom_prompt: customPrompt || '',
    })
    return res.data
  },
  async publish(id: number) {
    const res = await client.post(`/api/v1/articles/${id}/publish?publish_type=manual`)
    return res.data
  },
  async publishToPlatform(articleId: number, platformAccountId: number) {
    const res = await client.post(`/api/v1/articles/${articleId}/publish-to-platform`, {
      platform_account_id: platformAccountId,
    })
    return res.data
  },
  async preview(topicTitle: string, customPrompt?: string) {
    const res = await client.post('/api/v1/articles/preview', {
      topic_title: topicTitle,
      custom_prompt: customPrompt || '',
    })
    return res.data
  },
  async batchDelete(ids: number[]) {
    const res = await client.post('/api/v1/articles/batch_delete', { ids })
    return res.data
  },
}

export const topicsApi = {
  async getAll(params?: any) {
    const res = await client.get('/api/v1/topics/', { params })
    return res.data
  },
  async triggerCrawl() {
    const res = await client.post('/api/v1/topics/crawl')
    return res.data
  },
  async triggerCrawlForAccount(accountId: string) {
    const res = await client.post(`/api/v1/topics/crawl/${accountId}`)
    return res.data
  },
  async batchDelete(ids: number[]) {
    const res = await client.post('/api/v1/topics/batch_delete', { ids })
    return res.data
  },
}

// Celery 任务状态轮询: 前端提交任务后用它等任务完成
export const tasksApi = {
  async getStatus(taskId: string) {
    const res = await client.get(`/api/v1/tasks/${taskId}`)
    return res.data
  },
  async reloadBeat() {
    const res = await client.post('/api/v1/tasks/celery/reload-beat')
    return res.data
  },
}

// 平台账号相关 API（统一从 platforms.ts 导出，避免重复定义）
export {
  getAccounts,
  getAccount,
  getAccountTopics,
  getAccountArticles,
  createAccount,
  updateAccount,
  softDeleteAccount,
  restoreAccount,
  permanentDeleteAccount,
  updateAccountSchedule,
  platformAccountsApi,
} from './platforms'
