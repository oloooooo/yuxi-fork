// 资源市场的真实链路验证：停止使用后资源从列表消失，恢复后重新出现；不 mock 后端。
// 需要已登录会话，执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/marketplaceResourceLifecycle.js
async page => {
  const check = (value, reason) => { if (!value) throw new Error(reason) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const token = await page.evaluate(() => localStorage.getItem('user_token'))
  check(token, '需要登录会话，未登录时脚本不能运行')
  const headers = { Authorization: `Bearer ${token}` }
  const auth = await page.request.get(`${origin}/api/subscriptions/catalog`, { headers })
  check(auth.ok(), '需要登录会话，未登录时脚本不能运行')

  await page.goto(`${origin}/marketplace`)
  await page.getByRole('tab', { name: /Skill/ }).click()
  await page.locator('.ant-tabs-tabpane-active .ant-list-item').first().waitFor()

  const visibleItems = await page.request.get(`${origin}/api/subscriptions/catalog`, { headers })
  const catalog = (await visibleItems.json()).data
  const target = catalog.find(item => item.resource_type === 'skill' && item.visible)
  check(target, '当前账号没有可停止使用的 Skill')

  const card = page.locator('.ant-tabs-tabpane-active .ant-list-item', { hasText: target.resource_slug })
  await card.getByRole('button', { name: '停止使用' }).click()
  await page.locator('.ant-message').getByText(/已停止使用/).waitFor()
  await card.getByText('已停止使用').waitFor()
  check(await card.getByText('已停止使用').count() >= 1, '卡片未显示已停止使用')
  check(await card.getByRole('button', { name: '恢复使用' }).count() === 1, '卡片未给出恢复入口')

  const afterStop = await page.request.get(`${origin}/api/subscriptions/catalog`, { headers })
  const stopRow = (await afterStop.json()).data.find(item => item.resource_slug === target.resource_slug)
  check(stopRow?.status === 'disabled' && stopRow?.visible === false, '停止后目录仍标记为可见')

  await card.getByRole('button', { name: '恢复使用' }).click()
  await page.locator('.ant-message').getByText(/已恢复使用/).waitFor()
  await card.getByText('已可用').waitFor()
  check(await card.getByText('已可用').count() >= 1, '恢复后卡片未显示已可用')

  const afterResume = await page.request.get(`${origin}/api/subscriptions/catalog`, { headers })
  const resumeRow = (await afterResume.json()).data.find(item => item.resource_slug === target.resource_slug)
  check(resumeRow?.status !== 'disabled' && resumeRow?.visible === true, '恢复后目录仍标记为停用')

  await page.screenshot({ path: 'test-results/marketplace-resource-lifecycle.png', fullPage: true })
  return { skill: target.resource_slug, stoppedHidden: true, resumedVisible: true }
}
