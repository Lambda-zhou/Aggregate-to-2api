// 移动端 375×667 视口冒烟（v22 P1-4，纯前端静态产物，无后端依赖）
// 验证：landing 门户无横向滚动 + 关键标题渲染 + 首页可交互元素存在。
const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/Administrator.DESKTOP-EGNE9ND/Desktop/imagefree-2ai/frontend/node_modules/playwright-core');

const BASE = 'http://127.0.0.1:4590';
const VIEWPORT = { width: 375, height: 667 };
let pass = 0, fail = 0;
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
};

const BROWSERS = 'C:/Users/Administrator.DESKTOP-EGNE9ND/AppData/Local/ms-playwright';
function findChromium() {
  const dirs = fs.readdirSync(BROWSERS).filter(d => /^chromium-/.test(d) && !d.includes('headless')).sort().reverse();
  for (const d of dirs) for (const sub of ['chrome-win64', 'chrome-win']) {
    const exe = path.join(BROWSERS, d, sub, 'chrome.exe');
    if (fs.existsSync(exe)) return exe;
  }
  return null;
}

(async () => {
  const exe = findChromium();
  if (!exe) { console.log('FAIL 未找到 chromium 可执行文件'); process.exit(1); }
  console.log('chromium:', exe.split('\\').slice(-3).join('\\'));

  const browser = await chromium.launch({ executablePath: exe, headless: true });
  const page = await browser.newPage({ viewport: VIEWPORT });
  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 100)); });

  try {
    await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 30000 }).catch(() => page.goto(BASE + '/', { timeout: 30000 }));

    // 1. 无横向滚动
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    ok('无横向滚动 (scrollWidth<=375)', scrollWidth <= 375, `实际 scrollWidth=${scrollWidth}`);

    // 2. 关键标题渲染
    const titleText = await page.evaluate(() => {
      const el = document.querySelector('.brand-name') || document.querySelector('h1') || document.querySelector('.hero h1');
      return el ? el.textContent.trim() : null;
    });
    ok('关键标题渲染', !!titleText, `title=${titleText}`);

    // 3. 横向 overflow 元素计数（允许少量装饰性，报数量）
    const overflowCount = await page.evaluate(() =>
      [...document.querySelectorAll('*')].filter(e => e.scrollWidth > e.clientWidth + 1).length
    );
    ok('根级横向 overflow 极小 (<=5)', overflowCount <= 5, `实际 ${overflowCount} 个元素`);

    // 4. 工具卡/主要按钮存在
    const ctaExists = await page.evaluate(() =>
      !!document.querySelector('button, .btn, a.btn, [role="button"]')
    );
    ok('有可点击元素 (button/.btn)', ctaExists);

    // 5. 首页截图（供审查证据）
    await page.screenshot({ path: 'docs/verification-artifacts/mobile-landing-375.png', fullPage: true });
    console.log('  截图: docs/verification-artifacts/mobile-landing-375.png');
  } catch (e) {
    console.log('FAIL 页面访问异常: ' + e.message.slice(0, 200));
    fail++;
  } finally {
    if (consoleErrors.length) console.log('  console.error (' + consoleErrors.length + '): ' + consoleErrors.slice(0, 3).join(' || '));
    await browser.close();
  }

  console.log(`\n结果: ${pass} passed / ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
