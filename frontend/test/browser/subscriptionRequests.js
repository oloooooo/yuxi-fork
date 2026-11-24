// 订阅页面的真实 DOM 验证：目录申请、超管审批与角标；固定 HTTP 结果不替代后端集成测试。
async page => {
  const check = (value, reason) => { if (!value) throw new Error(reason) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const calls = { submit: [], approve: [], reject: [] }
  const catalog = [
    { resource_type: 'mcp', resource_slug: 'demo-connector', name: '演示连接器', description: '只读预览', visible: false, status: null, subscription_id: null },
    { resource_type: 'skill', resource_slug: 'demo-skill', name: '演示技能', description: '', visible: true, status: null, subscription_id: null }
  ]
  const pending = [{ id: 7, resource_type: 'mcp', resource_slug: 'demo-connector', name: '演示连接器', applicant_uid: 'alice', applicant_name: 'Alice', created_at: '2026-11-24T10:00:00+00:00' }]

  await page.route('**/api/subscriptions**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/catalog') && method === 'GET') return respond({ success: true, data: catalog })
    if (path.endsWith('/mine') && method === 'GET') return respond({ success: true, data: [] })
    if (path.endsWith('/pending') && method === 'GET') return respond({ success: true, data: { items: pending, count: pending.length } })
    if (path.endsWith('/pending/count') && method === 'GET') return respond({ success: true, data: { count: pending.length } })
    if (path.endsWith('/subscriptions') && method === 'POST') { calls.submit.push(request.postDataJSON()); return respond({ success: true, data: { id: 7, status: 'pending' } }) }
    if (path.endsWith('/7/approve') && method === 'POST') { calls.approve.push(7); pending.length = 0; return respond({ success: true, data: { id: 7 } }) }
    if (path.endsWith('/7/reject') && method === 'POST') { calls.reject.push(7); return respond({ success: true, data: { id: 7 } }) }
    return respond({ success: true, data: {} })
  })

  await page.goto(`${origin}/test/browser/fixtures/subscriptionRequests.html`)
  await page.setViewportSize({ width: 1280, height: 860 })

  const row = name => page.locator('.ant-list-item', { hasText: name })
  await row('演示连接器').getByRole('button', { name: '申请订阅' }).click()
  check(calls.submit.length === 1, '申请订阅没有发出请求')
  check(calls.submit[0].resource_type === 'mcp' && calls.submit[0].resource_slug === 'demo-connector', '申请参数不正确')
  check(await row('演示技能').getByText('已可用').count() === 1, '已可见资源没有显示已可用')
  check(await row('演示技能').getByRole('button', { name: '申请订阅' }).count() === 0, '已可见资源仍可申请')

  await page.getByRole('tab', { name: '待审批' }).click()
  await page.getByRole('button', { name: '批 准' }).click()
  check(calls.approve.length === 1 && calls.approve[0] === 7, '批准没有调用审批接口')
  await page.screenshot({ path: '/tmp/yuxi-subscription-panel.png', animations: 'disabled' })

  await page.setViewportSize({ width: 768, height: 860 })
  await page.screenshot({ path: '/tmp/yuxi-subscription-panel-narrow.png', animations: 'disabled' })
}
