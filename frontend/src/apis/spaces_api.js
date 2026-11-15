import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

/**
 * 业务空间内容发布：写入 Agent 只读挂载的 /spaces 前缀。
 * 空间标识必须与 Agent slug 相同，发布后才在该 Agent 的 /spaces 下可见。
 */
export const spacesApi = {
  /** 已发布的空间标识与文件数。 */
  list: () => apiAdminGet('/api/spaces'),

  /** 一个空间已发布的 Markdown 相对路径。 */
  files: (slug) => apiAdminGet(`/api/spaces/${encodeURIComponent(slug)}`),

  /** 批量发布 Markdown，entries 形如 [{ path, file }]。 */
  publish: (slug, entries) => {
    const form = new FormData()
    entries.forEach(({ path, file }) => {
      form.append('paths', path)
      form.append('files', file, path.split('/').pop())
    })
    return apiAdminPost(`/api/spaces/${encodeURIComponent(slug)}/files`, form)
  },

  /** 删除整个空间前缀。 */
  remove: (slug) => apiAdminDelete(`/api/spaces/${encodeURIComponent(slug)}`)
}
