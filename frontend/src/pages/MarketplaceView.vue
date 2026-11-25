<template>
  <div class="marketplace-view">
    <header class="marketplace-header">
      <div>
        <h1>资源市场</h1>
        <p>浏览可申请的智能体、Skill 与连接器，提交后由管理员审批。</p>
      </div>
      <a-button :loading="loading" @click="loadAll">
        <template #icon><RefreshCw /></template>
        刷新
      </a-button>
    </header>

    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane v-for="tab in resourceTabs" :key="tab.key" :tab="`${tab.label}（${itemsOf(tab.key).length}）`">
        <a-empty v-if="!itemsOf(tab.key).length" :description="`暂无可申请的${tab.label}`" />
        <a-list v-else :data-source="itemsOf(tab.key)" :grid="{ gutter: 16, column: 2 }" item-layout="horizontal">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card :title="item.name" size="small" class="marketplace-card">
                <template #extra>
                  <a-tag :color="statusColor(item.status)">{{ statusText(item.status) }}</a-tag>
                </template>
                <p class="marketplace-desc">{{ item.description || '暂无描述' }}</p>
                <a-button
                  v-if="item.status === 'disabled'"
                  size="small"
                  :loading="acting === `resume:${item.resource_slug}`"
                  @click="resume(item)"
                >
                  恢复使用
                </a-button>
                <a-button
                  v-else-if="item.visible"
                  size="small"
                  danger
                  :loading="acting === `stop:${item.resource_slug}`"
                  @click="stop(item)"
                >
                  停止使用
                </a-button>
                <a-button
                  v-else-if="item.status === 'pending'"
                  size="small"
                  :loading="withdrawing === item.subscription_id"
                  @click="withdraw(item.subscription_id)"
                >
                  撤回申请
                </a-button>
                <a-button
                  v-else
                  type="primary"
                  size="small"
                  :loading="submitting === item.resource_slug"
                  @click="apply(item)"
                >
                  {{ item.status === 'rejected' ? '重新申请' : '申请订阅' }}
                </a-button>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-empty v-if="!mine.length" description="还没有提交过申请" />
        <a-table v-else :data-source="mine" :columns="mineColumns" :pagination="false" row-key="id" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'">
              <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
            </template>
            <template v-else-if="column.key === 'actions'">
              <a-button
                v-if="record.status === 'disabled'"
                size="small"
                :loading="acting === `resume:${record.resource_slug}`"
                @click="resume(record)"
              >
                恢复使用
              </a-button>
              <a-button
                v-else
                size="small"
                danger
                :loading="withdrawing === record.id"
                @click="withdraw(record.id)"
              >
                撤回
              </a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="userStore.isAdmin" key="pending" tab="待审批">
        <a-empty v-if="!pending.length" description="没有待审批的申请" />
        <a-table v-else :data-source="pending" :columns="pendingColumns" :pagination="false" row-key="id" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'type'">{{ typeLabel(record.resource_type) }}</template>
            <template v-else-if="column.key === 'actions'">
              <a-space>
                <a-button size="small" type="primary" :loading="deciding === record.id" @click="decide(record, true)">
                  批准
                </a-button>
                <a-button size="small" danger :loading="deciding === record.id" @click="decide(record, false)">
                  驳回
                </a-button>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from '@lucide/vue'
import {
  approveSubscription,
  listMySubscriptions,
  listPendingSubscriptions,
  listSubscriptionCatalog,
  rejectSubscription,
  resumeUsingSubscription,
  revokeSubscription,
  stopUsingSubscription,
  submitSubscription
} from '@/apis/subscription_api'
import { useUserStore } from '@/modules/identity/model/user'

const userStore = useUserStore()

const resourceTabs = [
  { key: 'agent', label: '智能体' },
  { key: 'skill', label: 'Skill' },
  { key: 'mcp', label: '连接器' }
]

const mineColumns = [
  { title: '资源', dataIndex: 'name', key: 'name' },
  { title: '类型', dataIndex: 'resource_type', key: 'type', customRender: ({ record }) => typeLabel(record.resource_type) },
  { title: '状态', key: 'status' },
  { title: '提交时间', dataIndex: 'created_at', key: 'created_at' },
  { title: '操作', key: 'actions' }
]

const pendingColumns = [
  { title: '资源', dataIndex: 'name', key: 'name' },
  { title: '类型', key: 'type' },
  { title: '申请人', dataIndex: 'applicant_uid', key: 'applicant_uid' },
  { title: '提交时间', dataIndex: 'created_at', key: 'created_at' },
  { title: '操作', key: 'actions' }
]

const loading = ref(false)
const submitting = ref('')
const acting = ref('')
const withdrawing = ref(0)
const deciding = ref(0)
const activeTab = ref('agent')
const catalog = ref([])
const mine = ref([])
const pending = ref([])

const grouped = computed(() => {
  const map = { agent: [], skill: [], mcp: [] }
  for (const item of catalog.value) {
    if (!map[item.resource_type]) continue
    map[item.resource_type].push(item)
  }
  return map
})

const itemsOf = (type) => grouped.value[type] || []

const typeLabel = (type) =>
  ({ agent: '智能体', skill: 'Skill', mcp: '连接器', knowledge_create: '知识库权限' })[type] || type

const statusText = (status) =>
  ({ pending: '待审批', approved: '已可用', rejected: '已驳回', disabled: '已停止使用' })[status] || '未申请'

const statusColor = (status) =>
  ({ pending: 'orange', approved: 'green', rejected: 'red', disabled: 'default' })[status] || 'default'

const loadAll = async () => {
  loading.value = true
  try {
    const [catalogResult, mineResult] = await Promise.all([
      listSubscriptionCatalog(),
      listMySubscriptions()
    ])
    const catalogData = catalogResult?.data
    catalog.value = Array.isArray(catalogData) ? catalogData : catalogData?.items || []
    const mineData = mineResult?.data
    mine.value = Array.isArray(mineData) ? mineData : mineData?.items || []
    if (userStore.isAdmin) {
      const pendingData = (await listPendingSubscriptions())?.data
      pending.value = Array.isArray(pendingData) ? pendingData : pendingData?.items || []
    }
  } catch (error) {
    message.error(error?.message || '加载资源市场失败')
  } finally {
    loading.value = false
  }
}

const apply = async (item) => {
  submitting.value = item.resource_slug
  try {
    await submitSubscription(item.resource_type, item.resource_slug)
    message.success('申请已提交，等待管理员审批')
    await loadAll()
  } catch (error) {
    message.error(error?.message || '提交申请失败')
  } finally {
    submitting.value = ''
  }
}

const stop = async (item) => {
  acting.value = `stop:${item.resource_slug}`
  try {
    await stopUsingSubscription(item.resource_type, item.resource_slug)
    message.success('已停止使用，可在资源市场恢复')
    await loadAll()
  } catch (error) {
    message.warning(error?.message || '停止使用失败')
    await loadAll()
  } finally {
    acting.value = ''
  }
}

const resume = async (item) => {
  acting.value = `resume:${item.resource_slug}`
  try {
    await resumeUsingSubscription(item.resource_type, item.resource_slug)
    message.success('已恢复使用')
    await loadAll()
  } catch (error) {
    message.warning(error?.message || '恢复使用失败')
    await loadAll()
  } finally {
    acting.value = ''
  }
}

const withdraw = async (subscriptionId) => {
  withdrawing.value = subscriptionId
  try {
    await revokeSubscription(subscriptionId)
    message.success('已撤回申请')
    await loadAll()
  } catch (error) {
    message.warning(error?.message || '撤回申请失败，可能已被处理')
    await loadAll()
  } finally {
    withdrawing.value = 0
  }
}

const decide = async (record, approve) => {
  deciding.value = record.id
  try {
    if (approve) await approveSubscription(record.id)
    else await rejectSubscription(record.id)
    message.success(approve ? '已批准' : '已驳回')
    await loadAll()
  } catch (error) {
    message.warning(error?.message || '审批失败，申请可能已被撤回')
    await loadAll()
  } finally {
    deciding.value = 0
  }
}

onMounted(loadAll)
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
