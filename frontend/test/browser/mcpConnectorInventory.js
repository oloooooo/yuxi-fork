// 连接器页的真实链路验证：已注册的收费 MCP 出现在列表，详情页工具清单可读；不 mock 后端。
// 需要超级管理员登录会话，执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/mcpConnectorInventory.js
async page => {
  const check = (value, reason) => { if (!value) throw new Error(reason) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const token = await page.evaluate(() => localStorage.getItem('user_token'))
  check(token, '需要登录会话')
  const headers = { Authorization: `Bearer ${token}` }
  const auth = await page.request.get(`${origin}/api/system/mcp-servers`, { headers })
  check(auth.ok(), '需要超级管理员登录会话')

  const expected = ['vertical-search', 'bailian-webfetch', 'bailian-enhanced-search', 'bailian-weather']
  const listResponse = await page.request.get(`${origin}/api/system/mcp-servers`, { headers })
  check(listResponse.ok(), '读取连接器列表失败')
  const rows = (await listResponse.json()).data
  const slugs = rows.map(item => item.slug)
  for (const slug of expected) check(slugs.includes(slug), `连接器列表缺少 ${slug}`)

  await page.goto(`${origin}/extensions?tab=mcp`)
  await page.locator('.info-card').first().waitFor()
  for (const slug of expected) {
    check(await page.locator('.info-card', { hasText: slug }).count() >= 1, `页面未展示连接器 ${slug}`)
  }

  const target = rows.find(item => item.slug === 'vertical-search')
  check(target, '未找到 vertical-search 行')
  const tools = await page.request.get(`${origin}/api/system/mcp-servers/vertical-search/tools`, { headers })
  check(tools.ok(), '读取垂类搜索工具失败')
  const toolNames = (await tools.json()).data.map(item => item.name)
  check(toolNames.includes('poi_search') && toolNames.includes('medical_search'), '垂类搜索工具名不符合预期')

  await page.screenshot({ path: 'test-results/mcp-connector-inventory.png', fullPage: true })
  return { connectors: slugs.filter(slug => expected.includes(slug)), tools: toolNames }
}
