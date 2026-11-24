export function getShareConfigLabel(shareConfig, invalid = false) {
  if (invalid) return '共享配置无效'
  const config = shareConfig || {}
  const readScope = config.version === 2 ? config.read_scope : config
  const manageScope = config.manage_scope
  if (config.version === 2 && !config.read_scope && !manageScope) return '仅所有者'
  const scopeLabel = (scope) => {
    if (!scope) return '无'
    if (scope.access_level === 'global') return '全局'
    if (scope.access_level === 'department') return `部门(${scope.department_ids?.length || 0})`
    return `用户(${scope.user_uids?.length || 0})`
  }
  if (manageScope) return `读${scopeLabel(readScope)} · 管${scopeLabel(manageScope)}`
  // 部门范围的管理权属于该部门的管理员，这里不能标成“只读”。
  if (readScope?.access_level === 'department') return `${scopeLabel(readScope)} · 管理员可管`
  return `只读${scopeLabel(readScope)}`
}

/** 复制共享配置，坏配置仅在本地暂设为仅所有者，等待用户明确保存。 */
export function cloneShareConfig(shareConfig, invalid = false) {
  if (invalid) return { version: 2, read_scope: null, manage_scope: null }
  return shareConfig ? JSON.parse(JSON.stringify(shareConfig)) : null
}
