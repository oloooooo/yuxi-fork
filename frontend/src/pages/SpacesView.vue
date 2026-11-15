<template>
  <div class="spaces-view">
    <PageHeader title="业务空间" :loading="loading" show-border>
      <template #actions>
        <a-button size="small" @click="reload">刷新</a-button>
      </template>
    </PageHeader>

    <p class="spaces-intro">
      空间内容发布到 Agent 只读挂载的 <code>/spaces</code> 目录。发布后在 Agent 的「业务空间」选项里选中该标识，只接受 Markdown；同名文件再次发布会直接覆盖。
    </p>

    <section class="spaces-card">
      <h2>选择空间</h2>
      <div class="slug-row">
        <label for="space-slug">空间标识</label>
        <a-input
          id="space-slug"
          v-model:value="slug"
          class="slug-input"
          placeholder="hybrid-bonding"
          @press-enter="selectSpace"
        />
        <a-button type="primary" :disabled="!slug.trim()" @click="selectSpace">载入</a-button>
      </div>
      <div v-if="spaces.length" class="slug-chips">
        <button
          v-for="item in spaces"
          :key="item.slug"
          type="button"
          class="slug-chip"
          :class="{ active: item.slug === slug.trim() }"
          @click="slug = item.slug"
        >
          {{ item.slug }} <span class="slug-count">{{ item.files }}</span>
        </button>
      </div>
      <a-empty v-else :description="loading ? '加载中' : '还没有发布过任何空间'" />
    </section>

    <section class="spaces-card">
      <h2>发布内容</h2>
      <div
        class="drop-zone"
        :class="{ active: dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <p>把空间文件夹拖到这里，或</p>
        <a-button size="small" @click="folderInput?.click()">选择文件夹</a-button>
        <input
          ref="folderInput"
          type="file"
          webkitdirectory
          multiple
          hidden
          @change="onPickFolder"
        />
      </div>

      <p v-if="skipped" class="spaces-hint">已忽略 {{ skipped }} 个非 Markdown 文件。</p>

      <ul v-if="pending.length" class="pending-list">
        <li v-for="item in pending" :key="item.path">
          <span class="pending-path">{{ item.path }}</span>
          <span class="pending-size">{{ formatSize(item.file.size) }}</span>
        </li>
      </ul>
      <a-empty v-else description="还没有待发布的文件" />

      <div class="actions-row">
        <a-button
          type="primary"
          :loading="publishing"
          :disabled="!slug.trim() || pending.length === 0"
          @click="publish"
        >
          发布 {{ pending.length || '' }} 个文件
        </a-button>
        <a-button :disabled="pending.length === 0" @click="pending = []">清空</a-button>
      </div>
    </section>

    <section class="spaces-card">
      <h2>已发布文件</h2>
      <ul v-if="files.length" class="pending-list">
        <li v-for="path in files" :key="path">
          <span class="pending-path">{{ path }}</span>
        </li>
      </ul>
      <a-empty v-else description="这个空间下还没有文件" />

      <div v-if="files.length" class="actions-row">
        <a-popconfirm
          title="删除该空间下全部文件？"
          ok-text="删除"
          cancel-text="取消"
          @confirm="removeSpace"
        >
          <a-button danger :loading="deleting">删除整个空间</a-button>
        </a-popconfirm>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import { resolveApiErrorMessage } from '@/apis/base'
import { spacesApi } from '@/apis/spaces_api'

const slug = ref('')
const spaces = ref([])
const files = ref([])
const pending = ref([])
const skipped = ref(0)
const loading = ref(false)
const publishing = ref(false)
const deleting = ref(false)
const dragging = ref(false)
const folderInput = ref(null)

const isMarkdown = (path) => path.toLowerCase().endsWith('.md')

const formatSize = (size) =>
  size > 1024 ? `${Math.round(size / 1024)} KB` : `${size || 0} B`

/** 去掉拖入文件夹带来的公共根目录，发布路径相对空间本身。 */
function stripCommonRoot(entries) {
  const roots = new Set(entries.map((entry) => entry.path.split('/')[0]))
  if (roots.size !== 1) return entries
  return entries.map((entry) => ({ ...entry, path: entry.path.split('/').slice(1).join('/') }))
}

/** 合并本次选择，同路径以后选为准；非 Markdown 直接丢弃。 */
function addEntries(entries) {
  const markdown = stripCommonRoot(entries).filter((entry) => entry.path && isMarkdown(entry.path))
  skipped.value = entries.length - markdown.length
  const merged = new Map(pending.value.map((entry) => [entry.path, entry]))
  markdown.forEach((entry) => merged.set(entry.path, entry))
  pending.value = [...merged.values()].sort((a, b) => a.path.localeCompare(b.path))
}

function onPickFolder(event) {
  const picked = [...event.target.files].map((file) => ({
    path: file.webkitRelativePath || file.name,
    file
  }))
  addEntries(picked)
  event.target.value = ''
}

/** 递归读取目录项，保留相对路径。 */
async function readEntry(entry, prefix) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
    return [{ path: `${prefix}${file.name}`, file }]
  }
  const reader = entry.createReader()
  const children = []
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
    if (batch.length === 0) break
    children.push(...batch)
  }
  const nested = await Promise.all(
    children.map((child) => readEntry(child, `${prefix}${entry.name}/`))
  )
  return nested.flat()
}

async function onDrop(event) {
  dragging.value = false
  const items = [...(event.dataTransfer?.items || [])]
  const dropped = await Promise.all(
    items.map((item) => {
      const entry = item.webkitGetAsEntry?.()
      return entry ? readEntry(entry, '') : []
    })
  )
  addEntries(dropped.flat())
}

async function reload() {
  loading.value = true
  try {
    spaces.value = (await spacesApi.list()).spaces
  } catch (error) {
    message.error(resolveApiErrorMessage(error, '加载空间失败'))
  } finally {
    loading.value = false
  }
}

async function loadFiles() {
  try {
    files.value = (await spacesApi.files(slug.value.trim())).files
  } catch (error) {
    files.value = []
    message.error(resolveApiErrorMessage(error, '读取空间文件失败'))
  }
}

async function selectSpace() {
  if (!slug.value.trim()) return
  await loadFiles()
  if (!spaces.value.some((item) => item.slug === slug.value.trim())) {
    message.info('该空间还没有内容，发布后会自动创建')
  }
}

async function publish() {
  publishing.value = true
  try {
    const result = await spacesApi.publish(slug.value.trim(), pending.value)
    message.success(`已发布 ${result.saved.length} 个文件到 ${result.slug}`)
    pending.value = []
    skipped.value = 0
    await Promise.all([reload(), loadFiles()])
  } catch (error) {
    message.error(resolveApiErrorMessage(error, '发布失败'))
  } finally {
    publishing.value = false
  }
}

async function removeSpace() {
  deleting.value = true
  try {
    await spacesApi.remove(slug.value.trim())
    message.success('空间已清空')
    files.value = []
    await reload()
  } catch (error) {
    message.error(resolveApiErrorMessage(error, '删除失败'))
  } finally {
    deleting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.spaces-view {
  padding: 16px 24px 48px;
  max-width: 880px;
}

.spaces-intro {
  color: var(--color-text-secondary, #666);
  font-size: 13px;
  line-height: 1.7;
  margin: 12px 0 20px;
}

.spaces-card {
  border: 1px solid var(--border-surface, #e5e7eb);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 16px;
}

.spaces-card h2 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 12px;
}

.slug-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.slug-input {
  max-width: 260px;
}

.slug-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.slug-chip {
  border: 1px solid var(--border-surface, #e5e7eb);
  border-radius: 999px;
  background: transparent;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

.slug-chip.active {
  border-color: var(--main-color, #1677ff);
  color: var(--main-color, #1677ff);
}

.slug-count {
  color: var(--color-text-secondary, #888);
  margin-left: 4px;
}

.drop-zone {
  border: 1px dashed var(--border-surface, #d1d5db);
  border-radius: 10px;
  padding: 24px;
  text-align: center;
  color: var(--color-text-secondary, #666);
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}

.drop-zone.active {
  border-color: var(--main-color, #1677ff);
  background: rgba(22, 119, 255, 0.04);
}

.spaces-hint {
  font-size: 12px;
  color: #d97706;
  margin: 8px 0 0;
}

.pending-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
  max-height: 260px;
  overflow: auto;
  font-size: 13px;
}

.pending-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  border-bottom: 1px solid var(--border-surface, #f1f5f9);
}

.pending-path {
  font-family: var(--mono-font, ui-monospace, monospace);
  overflow-wrap: anywhere;
}

.pending-size {
  color: var(--color-text-secondary, #888);
  white-space: nowrap;
}

.actions-row {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
