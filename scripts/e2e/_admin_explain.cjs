const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0,80)); });
  page.on('pageerror', e => errors.push('PAGEERR '+e.message.slice(0,80)));
  // 访问 /admin Agent 页
  await page.goto('https://imagefree.hwhcie.bond/admin/agent', { waitUntil: 'networkidle', timeout: 60000 }).catch(e=>errors.push('goto '+e.message));
  await page.waitForTimeout(4000);
  const body = (await page.locator('body').textContent().catch(()=>'')||'');
  const hasAgentPage = body.includes('智能体') || body.includes('Agent');
  // 若有历史 run，检查 dag-trace-explain 出现
  const explainVisible = await page.locator('.dag-trace-explain').count().catch(()=>0);
  const traceBlocks = await page.locator('.dag-trace-block').count().catch(()=>0);
  console.log(JSON.stringify({ hasAgentPage, explainVisible, traceBlocks, bodyPreview: body.slice(0,120), errors: errors.slice(0,5) }, null, 2));
  await browser.close();
})();
