<template>
  <div class="subscription-panel">
    <a-tabs v-model:activeKey="activeTab">
      <a-tab-pane key="catalog" tab="资源目录">
        <a-spin :spinning="loading">
          <a-empty v-if="!catalog.length" description="暂无可申请资源" />
          <a-list v-else :data-source="catalog" item-layout="horizontal" class="subscription-list">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta>
                  <template #title>
                    {{ item.name }}
                    <a-tag>{{ typeLabels[item.resource_type] || item.resource_type }}</a-tag>
                    <a-tag v-if="item.status" :color="statusColors[item.status]">
                      {{ statusLabels[item.status] }}
                    </a-tag>
                  </template>
                  <template #description>
                    <span class="muted">{{ item.resource_slug }}</span>
                    <span v-if="item.description"> · {{ item.description }}</span>
                  </template>
                </a-list-item-meta>
                <a-button
                  v-if="!item.visible && !item.status"
                  type="primary"
                  size="small"
                  @click="apply(item)"
                >
                  申请订阅
                </a-button>
                <a-button
                  v-else-if="item.status === 'rejected'"
                  size="small"
                  @click="apply(item)"
                >
                  重新申请
                </a-button>
                <a-tag v-else-if="item.visible" color="green">已可用</a-tag>
              </a-list-item>
            </template>
          </a-list>
        </a-spin>
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-table
          :data-source="mine"
          :columns="mineColumns"
          :pagination="false"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'action'">
              <a-button v-if="record.status === 'approved'" danger size="small" @click="revoke(record)">
                撤销
              </a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="userStore.isSuperAdmin" key="pending" tab="待审批">
        <a-empty v-if="!pending.length" description="没有待处理申请" />
        <a-table
          v-else
          :data-source="pending"
          :columns="pendingColumns"
          :pagination="false"
          row-key="id"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'action'">
              <a-space>
                <a-button type="primary" size="small" @click="decide(record, true)">批准</a-button>
                <a-button size="small" @click="decide(record, false)">驳回</a-button>
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
import { useUserStore } from '@/modules/identity'
import {
  approveSubscription,
  getPendingSubscriptionCount,
  listMySubscriptions,
  listPendingSubscriptions,
  listSubscriptionCatalog,
  rejectSubscription,
  revokeSubscription,
  submitSubscription
} from '@/apis/subscription_api'

const emit = defineEmits(['pending-change'])
const userStore = useUserStore()

const activeTab = ref('catalog')
const loading = ref(false)
const catalog = ref([])
const mine = ref([])
const pending = ref([])

const typeLabels = { mcp: '连接器', agent: '智能体', skill: '技能' }
const statusLabels = { pending: '待审批', approved: '已批准', rejected: '已驳回' }
const statusColors = { pending: 'gold', approved: 'green', rejected: 'red' }

const mineColumns = [
  { title: '资源', dataIndex: 'name', key: 'name' },
  { title: '标识', dataIndex: 'resource_slug', key: 'resource_slug' },
  { title: '状态', dataIndex: 'status', key: 'status', customRender: ({ text }) => statusLabels[text] || text },
  { title: '', key: 'action', width: 90 }
]

const pendingColumns = [
  { title: '资源', dataIndex: 'name', key: 'name' },
  { title: '类型', dataIndex: 'resource_type', key: 'resource_type', customRender: ({ text }) => typeLabels[text] || text },
  { title: '申请人', dataIndex: 'applicant_uid', key: 'applicant_uid' },
  { title: '提交时间', dataIndex: 'created_at', key: 'created_at' },
  { title: '', key: 'action', width: 150 }
]

const hasPending = computed(() => pending.value.length > 0)

async function loadAll() {
  loading.value = true
  try {
    const [catalogResult, mineResult] = await Promise.all([listSubscriptionCatalog(), listMySubscriptions()])
    catalog.value = catalogResult.data || []
    mine.value = mineResult.data || []
    if (userStore.isSuperAdmin) {
      const pendingResult = await listPendingSubscriptions()
      pending.value = pendingResult.data?.items || []
      emit('pending-change', await getPendingSubscriptionCount())
    }
  } catch (error) {
    message.error(error?.detail || '订阅信息加载失败')
  } finally {
    loading.value = false
  }
}

async function apply(item) {
  try {
    await submitSubscription(item.resource_type, item.resource_slug)
    message.success('申请已提交，等待超级管理员审批')
    await loadAll()
  } catch (error) {
    message.error(error?.detail || '申请提交失败')
  }
}

async function revoke(record) {
  try {
    await revokeSubscription(record.id)
    message.success('已撤销订阅')
    await loadAll()
  } catch (error) {
    message.error(error?.detail || '撤销失败')
  }
}

async function decide(record, approve) {
  try {
    if (approve) await approveSubscription(record.id)
    else await rejectSubscription(record.id)
    message.success(approve ? '已批准' : '已驳回')
    await loadAll()
  } catch (error) {
    message.error(error?.detail || '审批失败')
  }
}

onMounted(loadAll)
defineExpose({ hasPending, loadAll })
</script>

<style scoped>
.subscription-panel {
  padding: 0 4px;
}
.subscription-list :deep(.ant-list-item-meta-description) {
  word-break: break-all;
}
.muted {
  color: #8c8c8c;
}
</style>
