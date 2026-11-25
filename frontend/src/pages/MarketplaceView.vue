<template>
  <div class="marketplace-view">
    <header class="marketplace-header">
      <div>
        <h1>资源市场</h1>
        <p>浏览可申请的智能体、Skill 与连接器，提交后由管理员审批。</p>
      </div>
      <a-button :loading="loading" @click="loadCatalog">
        <template #icon><RefreshCw /></template>
        刷新
      </a-button>
    </header>

    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane v-for="tab in tabs" :key="tab.key" :tab="`${tab.label}（${itemsOf(tab.key).length}）`">
        <a-empty v-if="!itemsOf(tab.key).length" :description="`暂无可申请的${tab.label}`" />
        <a-list v-else :data-source="itemsOf(tab.key)" :grid="{ gutter: 16, column: 2 }" item-layout="horizontal">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card :title="item.name" size="small" class="marketplace-card">
                <template #extra>
                  <a-tag :color="statusColor(item)">{{ statusText(item) }}</a-tag>
                </template>
                <p class="marketplace-desc">{{ item.description || '暂无描述' }}</p>
                <a-button
                  v-if="canApply(item)"
                  type="primary"
                  size="small"
                  :loading="submitting === item.resource_slug"
                  @click="apply(item)"
                >
                  {{ item.status === 'rejected' ? '重新申请' : '申请订阅' }}
                </a-button>
                <span v-else-if="item.status === 'pending'" class="marketplace-hint">等待管理员审批</span>
                <span v-else-if="item.visible" class="marketplace-hint">已可在对话与扩展页使用</span>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from '@lucide/vue'
import { listSubscriptionCatalog, submitSubscription } from '@/apis/subscription_api'

const tabs = [
  { key: 'agent', label: '智能体' },
  { key: 'skill', label: 'Skill' },
  { key: 'mcp', label: '连接器' }
]

const loading = ref(false)
const submitting = ref('')
const activeTab = ref('agent')
const catalog = ref([])

const grouped = computed(() => {
  const map = { agent: [], skill: [], mcp: [] }
  for (const item of catalog.value) {
    if (map[item.resource_type]) map[item.resource_type].push(item)
  }
  return map
})

const itemsOf = (type) => grouped.value[type] || []

const statusText = (item) => {
  if (item.visible) return '已可用'
  if (item.status === 'pending') return '待审批'
  if (item.status === 'approved') return '已批准'
  if (item.status === 'rejected') return '已驳回'
  return '未申请'
}

const statusColor = (item) => {
  if (item.visible) return 'green'
  if (item.status === 'pending') return 'orange'
  if (item.status === 'rejected') return 'red'
  return 'default'
}

const canApply = (item) => !item.visible && item.status !== 'pending'

const loadCatalog = async () => {
  loading.value = true
  try {
    const result = await listSubscriptionCatalog()
    const data = result?.data
    catalog.value = Array.isArray(data) ? data : data?.items || []
  } catch (error) {
    message.error(error?.message || '加载资源目录失败')
  } finally {
    loading.value = false
  }
}

const apply = async (item) => {
  submitting.value = item.resource_slug
  try {
    await submitSubscription(item.resource_type, item.resource_slug)
    message.success('申请已提交，等待管理员审批')
    await loadCatalog()
  } catch (error) {
    message.error(error?.message || '提交申请失败')
  } finally {
    submitting.value = ''
  }
}

onMounted(loadCatalog)
</script>

<style scoped>
.marketplace-view {
  padding: 24px 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.marketplace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.marketplace-header h1 {
  margin: 0 0 4px;
  font-size: 20px;
}

.marketplace-header p {
  margin: 0;
  color: var(--ant-color-text-secondary);
}

.marketplace-card {
  height: 100%;
}

.marketplace-desc {
  color: var(--ant-color-text-secondary);
  margin-bottom: 12px;
  min-height: 40px;
}

.marketplace-hint {
  color: var(--ant-color-text-secondary);
  font-size: 12px;
}
</style>
