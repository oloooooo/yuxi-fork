<template>
  <a-modal
    v-model:open="visible"
    :footer="null"
    :mask-closable="false"
    :closable="!busy"
    :body-style="{ maxHeight: 'min(70dvh, 620px)', overflowY: 'auto' }"
    width="min(640px, calc(100vw - 32px))"
    centered
    class="mcp-wizard"
    @cancel="close"
  >
    <template #title>
      <div class="wizard-title">
        <div>
          <strong>补齐连接器</strong>
          <p>这些连接器由 Skill 引入，创建后才能使用它提供的工具</p>
        </div>
        <span class="wizard-progress">{{ progressLabel }}</span>
      </div>
    </template>

    <div v-if="onSummary" class="wizard-summary">
      <p class="wizard-summary-hint">确认本次处理结果，创建好的连接器会立即可用。</p>
      <ul>
        <li v-for="item in results" :key="item.slug" :class="item.status">
          <span class="wizard-summary-slug">{{ item.slug }}</span>
          <span class="wizard-summary-status">{{
            { created: '已创建', skipped: '已跳过', failed: '创建失败', pending: '未处理' }[item.status]
          }}</span>
        </li>
      </ul>
    </div>

    <div v-else class="wizard-body">
      <a-alert v-if="currentStatus === 'created'" type="success" show-icon message="该连接器已创建" />
      <a-alert v-else type="info" show-icon :message="hint" />
      <a-form layout="vertical" class="extension-form">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="如：垂类搜索" :disabled="busy" />
        </a-form-item>
        <a-form-item label="标识">
          <a-input :value="current.slug" disabled />
          <div class="wizard-hint">需与 Skill 声明的依赖标识一致，不能修改。</div>
        </a-form-item>
        <a-form-item label="传输方式">
          <a-select v-model:value="form.transport" :disabled="busy">
            <a-select-option value="streamable_http">Streamable HTTP</a-select-option>
            <a-select-option value="sse">SSE</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="服务地址" required>
          <a-input v-model:value="form.url" placeholder="https://example.com/mcp" :disabled="busy" />
        </a-form-item>
        <a-form-item label="访问令牌（选填）" extra="仅支持 Bearer 令牌或无认证；API Key、OAuth 连接器请先在连接器页创建。">
          <a-input-password
            v-model:value="form.token"
            placeholder="留空表示无需认证"
            autocomplete="new-password"
            :disabled="busy"
          />
        </a-form-item>
        <a-form-item label="描述（选填）">
          <a-input v-model:value="form.description" placeholder="一句话说明这个服务能做什么" :disabled="busy" />
        </a-form-item>
      </a-form>
    </div>

    <footer class="wizard-footer">
      <a-button :disabled="busy || index === 0 || onSummary" @click="index -= 1">上一项</a-button>
      <a-button v-if="!onSummary" :disabled="busy" @click="skip">下一项</a-button>
      <span class="wizard-spacer" />
      <a-button v-if="onSummary" type="primary" @click="close">完成</a-button>
      <a-button v-else type="primary" :loading="busy" @click="advance">
        {{ primaryLabel }}
      </a-button>
    </footer>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'
import { resolveApiErrorMessage } from '@/apis/base'

const props = defineProps({
  open: { type: Boolean, default: false },
  items: { type: Array, default: () => [] },
  initialShareConfig: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'finished'])

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})
const index = ref(0)
const busy = ref(false)
const results = ref([])
const form = ref({})
const savedForms = ref({})

const current = computed(() => props.items[index.value] || {})
const onSummary = computed(() => index.value >= props.items.length)
const totalCount = computed(() => props.items.length)
const processedCount = computed(() => results.value.filter((item) => item.status !== 'pending').length)
const progressLabel = computed(() =>
  onSummary.value
    ? `已处理 ${processedCount.value} / ${totalCount.value} 项`
    : `待处理 ${totalCount.value - processedCount.value} / ${totalCount.value} 项`
)
const hint = computed(() => {
  const owner = current.value.skillName
  const position = `${index.value + 1}/${props.items.length}`
  return owner
    ? `Skill「${owner}」依赖连接器 ${current.value.slug}（${position}）`
    : `依赖连接器 ${current.value.slug}（${position}）`
})
const currentStatus = computed(() => results.value[index.value]?.status || 'pending')
const primaryLabel = computed(() => {
  if (currentStatus.value === 'created') return '继续'
  return index.value === props.items.length - 1 ? '创建并完成' : '创建并继续'
})
const advance = () => {
  if (currentStatus.value === 'created') {
    index.value += 1
    return
  }
  createAndNext()
}

const cloneScope = (scope) => (scope ? JSON.parse(JSON.stringify(scope)) : null)

const resetForm = () => {
  savedForms.value = {}
  form.value = formFor(current.value)
}

const formFor = (item) => {
  if (!item?.slug) return {}
  if (savedForms.value[item.slug]) return JSON.parse(JSON.stringify(savedForms.value[item.slug]))
  return {
    name: item.name || item.slug,
    transport: 'streamable_http',
    url: '',
    token: '',
    description: '',
    shareConfig: cloneScope(props.initialShareConfig)
  }
}

const reset = () => {
  index.value = 0
  busy.value = false
  results.value = props.items.map((item) => ({ slug: item.slug, status: 'pending' }))
  resetForm()
}

watch(
  () => [props.open, props.items],
  () => {
    if (props.open) reset()
  }
)

watch(index, () => {
  if (!onSummary.value) form.value = formFor(current.value)
})

const markCurrent = (status) => {
  results.value = results.value.map((item, position) =>
    position === index.value ? { ...item, status } : item
  )
}

const skip = () => {
  markCurrent('skipped')
  index.value += 1
}

const rememberForm = () => {
  if (current.value?.slug) savedForms.value[current.value.slug] = JSON.parse(JSON.stringify(form.value))
}

const createAndNext = async () => {
  rememberForm()
  if (!form.value.name?.trim()) {
    message.error('请填写名称')
    return
  }
  if (!/^https?:\/\/\S+$/i.test(form.value.url?.trim() || '')) {
    message.error('请填写合法的服务地址（http/https）')
    return
  }
  busy.value = true
  try {
    await mcpApi.createMcpServer({
      slug: current.value.slug,
      name: form.value.name.trim(),
      transport: form.value.transport,
      url: form.value.url.trim(),
      description: form.value.description?.trim() || undefined,
      auth_mode: form.value.token ? 'headers' : 'none',
      headers: form.value.token ? { Authorization: `Bearer ${form.value.token.trim()}` } : undefined,
      share_config: form.value.shareConfig || undefined
    })
    markCurrent('created')
    message.success(`连接器 ${current.value.slug} 已创建`)
    index.value += 1
  } catch (err) {
    markCurrent('failed')
    message.error(resolveApiErrorMessage(err, '创建连接器失败'))
    index.value += 1
  } finally {
    busy.value = false
  }
}

const close = () => {
  emit('finished', {
    created: results.value.filter((item) => item.status === 'created').map((item) => item.slug),
    skipped: results.value.filter((item) => item.status === 'skipped').map((item) => item.slug),
    failed: results.value.filter((item) => item.status === 'failed').map((item) => item.slug)
  })
  visible.value = false
}
</script>

<style scoped>
.wizard-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.wizard-title strong {
  font-size: 16px;
}
.wizard-title p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary, #667085);
}
.wizard-progress {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: 999px;
  background: #eef2ff;
  color: #2f5bff;
  font-size: 12px;
  font-weight: 600;
}
.wizard-body :deep(.ant-alert) {
  margin-bottom: 16px;
}
.wizard-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary, #667085);
}
.wizard-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid #f0f0f0;
}
.wizard-spacer {
  flex: 1;
}
.wizard-summary-hint {
  margin: 0 0 12px;
  color: var(--text-secondary, #667085);
  font-size: 13px;
}
.wizard-summary ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.wizard-summary li {
  display: flex;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fafafa;
  margin-bottom: 8px;
}
.wizard-summary li.created .wizard-summary-status {
  color: #12a06a;
}
.wizard-summary li.skipped .wizard-summary-status {
  color: #b26a00;
}
.wizard-summary li.failed .wizard-summary-status {
  color: #e1463c;
}
</style>
