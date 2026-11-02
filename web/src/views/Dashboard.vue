<template>
  <div class="dashboard">
    <div class="page-header">
      <h2>📊 系统概览</h2>
      <p>实时监控系统运行状态和数据统计</p>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="24" class="stat-cards">
      <el-col :span="6">
        <div class="stat-card stat-card-primary" @click="$router.push('/articles/hot-report')">
          <div class="stat-icon">
            <el-icon><TrendCharts /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-num">{{ stats.topicCount }}</div>
            <div class="stat-label">今日热搜话题</div>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card stat-card-success" @click="$router.push('/articles/hot-report')">
          <div class="stat-icon">
            <el-icon><Document /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-num">{{ stats.articleCount }}</div>
            <div class="stat-label">今日生成文章</div>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card stat-card-warning" @click="$router.push('/articles/hot-report')">
          <div class="stat-icon">
            <el-icon><Upload /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-num">{{ stats.publishedCount }}</div>
            <div class="stat-label">今日已发布</div>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card stat-card-info" @click="$router.push('/articles/keyword')">
          <div class="stat-icon">
            <el-icon><Collection /></el-icon>
          </div>
          <div class="stat-content">
            <div class="stat-num">{{ stats.keywordCount }}</div>
            <div class="stat-label">深度文章</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 内容区域 -->
    <el-row :gutter="24" style="margin-top: 24px">
      <el-col :span="14">
        <el-card class="content-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span class="card-title">📋 最近文章</span>
              <el-button type="primary" link @click="$router.push('/articles/hot-report')">
                查看全部 <el-icon class="el-icon--right"><ArrowRight /></el-icon>
              </el-button>
            </div>
          </template>
          <el-table :data="recentArticles" stripe size="small" max-height="320">
            <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small" effect="light">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="create_time" label="创建时间" width="140">
              <template #default="{ row }">
                <span class="time-text">{{ formatTime(row.create_time) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card class="content-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span class="card-title">⚡ 快捷操作</span>
            </div>
          </template>
          <div class="quick-actions">
            <div class="action-item" @click="triggerCrawl">
              <div class="action-icon" style="background: linear-gradient(135deg, #409EFF 0%, #337ecc 100%)">
                <el-icon><RefreshRight /></el-icon>
              </div>
              <div class="action-content">
                <div class="action-title">立即爬取</div>
                <div class="action-desc">抓取百度热搜话题</div>
              </div>
              <el-icon class="action-arrow"><ArrowRight /></el-icon>
            </div>
            <div class="action-item" @click="$router.push('/articles/hot-report')">
              <div class="action-icon" style="background: linear-gradient(135deg, #67C23A 0%, #529b2e 100%)">
                <el-icon><Edit /></el-icon>
              </div>
              <div class="action-content">
                <div class="action-title">生成热搜早报</div>
                <div class="action-desc">AI 整合今日热搜</div>
              </div>
              <el-icon class="action-arrow"><ArrowRight /></el-icon>
            </div>
            <div class="action-item" @click="$router.push('/articles/keyword')">
              <div class="action-icon" style="background: linear-gradient(135deg, #E6A23C 0%, #cf9232 100%)">
                <el-icon><DocumentAdd /></el-icon>
              </div>
              <div class="action-content">
                <div class="action-title">生成深度文章</div>
                <div class="action-desc">关键词深度解读</div>
              </div>
              <el-icon class="action-arrow"><ArrowRight /></el-icon>
            </div>
            <div class="action-item" @click="$router.push('/platform-accounts')">
              <div class="action-icon" style="background: linear-gradient(135deg, #909399 0%, #73767a 100%)">
                <el-icon><Connection /></el-icon>
              </div>
              <div class="action-content">
                <div class="action-title">管理平台账号</div>
                <div class="action-desc">配置多平台发布</div>
              </div>
              <el-icon class="action-arrow"><ArrowRight /></el-icon>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { articlesApi, topicsApi } from '@/api/articles'
import { keywordArticlesApi } from '@/api/keywordArticles'
import { ElMessage } from 'element-plus'

// 分页大小常量
const PAGE_SIZE_RECENT_ARTICLES = 10
const PAGE_SIZE_RECENT_KEYWORD = 5
const PAGE_SIZE_TOPICS = 50

const stats = reactive({ topicCount: 0, articleCount: 0, publishedCount: 0, keywordCount: 0 })
const recentArticles = ref<any[]>([])
const crawling = ref(false)

function statusType(status: string) {
  const map: Record<string, string> = {
    generated: 'success',
    published: '',
    draft: 'info',
    failed: 'danger',
    generating: 'warning',
  }
  return map[status] || 'info'
}

function statusText(status: string) {
  const map: Record<string, string> = {
    generated: '已生成',
    published: '已发布',
    draft: '草稿',
    failed: '失败',
    generating: '生成中',
  }
  return map[status] || status
}

function formatTime(time: string) {
  if (!time) return '-'
  return time.slice(0, 16).replace('T', ' ')
}

async function loadData() {
  try {
    const [artRes, kwRes] = await Promise.all([
      articlesApi.getAll({ page_size: PAGE_SIZE_RECENT_ARTICLES }),
      keywordArticlesApi.getAll({ page_size: PAGE_SIZE_RECENT_KEYWORD }),
    ])
    recentArticles.value = artRes.items || []
    stats.articleCount = artRes.total || 0
    stats.publishedCount = (artRes.items || []).filter((a: any) => a.status === 'published').length
    stats.keywordCount = kwRes.total || 0
    try {
      const topRes = await topicsApi.getAll({ page_size: PAGE_SIZE_TOPICS })
      stats.topicCount = topRes.total || 0
    } catch { stats.topicCount = 0 }
  } catch (e: any) {
    ElMessage.error('加载数据失败: ' + (e?.message || e?.response?.data?.message || '未知错误'))
  }
}

async function triggerCrawl() {
  crawling.value = true
  try {
    await topicsApi.triggerCrawl()
    ElMessage.success('爬取任务已提交')
    setTimeout(loadData, 2000)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '爬取失败')
  } finally {
    crawling.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.dashboard {
  max-width: 1400px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h2 {
  margin: 0 0 8px 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.page-header p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

/* 统计卡片 */
.stat-cards .el-col {
  margin-bottom: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
  background: #fff;
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all var(--transition-normal);
  box-shadow: var(--shadow-sm);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.stat-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #fff;
  flex-shrink: 0;
}

.stat-card-primary .stat-icon {
  background: linear-gradient(135deg, #409EFF 0%, #337ecc 100%);
}

.stat-card-success .stat-icon {
  background: linear-gradient(135deg, #67C23A 0%, #529b2e 100%);
}

.stat-card-warning .stat-icon {
  background: linear-gradient(135deg, #E6A23C 0%, #cf9232 100%);
}

.stat-card-info .stat-icon {
  background: linear-gradient(135deg, #909399 0%, #73767a 100%);
}

.stat-content {
  flex: 1;
  min-width: 0;
}

.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.stat-label {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-top: 4px;
}

/* 内容卡片 */
.content-card {
  border-radius: var(--radius-lg);
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.time-text {
  color: var(--color-text-secondary);
  font-size: 12px;
}

/* 快捷操作 */
.quick-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.action-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  background: var(--color-bg-page);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.action-item:hover {
  background: var(--color-primary-lighter);
  transform: translateX(4px);
}

.action-item:hover .action-arrow {
  color: var(--color-primary);
}

.action-icon {
  width: 42px;
  height: 42px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: #fff;
  flex-shrink: 0;
}

.action-content {
  flex: 1;
  min-width: 0;
}

.action-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.action-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

.action-arrow {
  color: var(--color-text-secondary);
  font-size: 14px;
  transition: color var(--transition-fast);
}
</style>
