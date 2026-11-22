<template>
  <a-modal
    v-model:open="visible"
    width="min(560px, calc(100vw - 32px))"
    centered
    :mask-closable="false"
    :closable="!submitting"
    :keyboard="!submitting"
    class="mcp-quick-modal"
    @ok="handleSubmit"
    :ok-text="submitting ? '正在创建' : '创建并继续安装'"
    :confirm-loading="submitting"
  >
    <template #title>
      <div class="quick-heading">
        <span class="quick-icon"><Plug :size="18" aria-hidden="true" /></span>
        <div>
          <span>补齐缺失的连接器</span>
          <p>这个 Skill 需要连接器才能使用它提供的工具</p>
        </div>
      </div>
    </template>

    <a-alert
      v-if="dependency?.skillName"
      class="quick-alert"
      type="info"
      show-icon
      :message="`Skill「${dependency.skillName}」依赖连接器 ${dependency.slug}`"
      description="填写该连接器的接入信息后会自动继续安装；之后也可以在「扩展 → 连接器」里修改。"
    />

    <a-form layout="vertical" class="quick-form" :disabled="submitting">
      <a-form-item label="名称" html-for="quick-mcp-name" required>
        <a-input id="quick-mcp-name" v-model:value="form.name" placeholder="如：垂类搜索" />
      </a-form-item>
      <a-form-item label="服务地址" html-for="quick-mcp-url" required>
        <a-input id="quick-mcp-url" v-model:value="form.url" placeholder="https://example.com/api/v1/mcps/xxx/mcp" />
        <div class="quick-hint">MCP 服务的 Streamable HTTP 地址，通常以 /mcp 结尾。</div>
      </a-form-item>
      <a-form-item label="访问令牌" html-for="quick-mcp-token">
        <a-input-password
          id="quick-mcp-token"
          v-model:value="form.token"
          placeholder="可选，留空表示无需认证"
          autocomplete="off"
        />
        <div class="quick-hint">填写后以 Authorization: Bearer 发送，所有使用该连接器的用户共用这个令牌。</div>
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Plug } from '@lucide/vue'
import { mcpApi } from '@/apis/mcp_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  dependency: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'created'])

const submitting = ref(false)
const form = reactive({ name: '', url: '', token: '' })

const visible = computed({
  get: () => props.open,
  set: (val) => { if (!submitting.value) emit('update:open', val) }
})

watch(
  () => props.open,
  (val) => {
    if (!val) return
    Object.assign(form, { name: props.dependency?.name || '', url: '', token: '' })
  }
)

const handleSubmit = async () => {
  if (submitting.value) return
  const slug = props.dependency?.slug
  if (!slug) return
  if (!form.name.trim()) {
    message.error('请填写连接器名称')
    return
  }
  if (!/^https?:\/\/\S+$/i.test(form.url.trim())) {
    message.error('服务地址必须是 http(s) 链接')
    return
  }
  submitting.value = true
  try {
    const token = form.token.trim()
    const result = await mcpApi.createMcpServer({
      slug,
      name: form.name.trim(),
      transport: 'streamable_http',
      url: form.url.trim(),
      auth_mode: token ? 'headers' : 'none',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined
    })
    if (!result.success) {
      message.error(result.message || '创建连接器失败')
      return
    }
    message.success('连接器创建成功')
    emit('update:open', false)
    emit('created', result.data)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.quick-heading {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.quick-heading p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--color-text-3);
}

.quick-icon {
  display: inline-flex;
  padding: 6px;
  border-radius: 8px;
  background: var(--color-primary-50, #eef4ff);
  color: var(--color-primary);
}

.quick-alert {
  margin-bottom: 16px;
}

.quick-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-3);
}
</style>
