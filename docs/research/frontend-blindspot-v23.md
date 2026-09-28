# 前端盲点核查报告 · N4（v23 用户路径盲点）

- 审计范围：`frontend/src/pages/`（13 页 + ApiGuide）、`components/`、`hooks/`、`api/`、`landing/src/`
- 方法：静态阅读（JSX + hooks + api 层 + 后端鉴权端点交叉核对），未跑浏览器
- 结论：整体三态基建成熟（useApi/Feedback/EmptyState/ErrorRetry/Toast 齐全），主要盲点集中在**鉴权缺失时被吞错/卡死**、**个别硬编码跳转**、**信息矛盾**与 **i18n 渐进缺口**；未发现会丢数据的毁灭性缺陷。

---

## 1. 空态 / 加载态 / 错误态 + 重试（逐列表页）

| 页面 | loading 骨架 | empty 空态 | error+重试 | 备注 |
|---|---|---|---|---|
| Tasks.tsx | ✅ rows 骨架 (L92) | ✅ EmptyState+CTA (L115) | ✅ ErrorRetry (L56) | CTA 硬编码跳转，见 §6-A |
| DLQ.tsx | ✅ rows 骨架 (L93) | ✅ EmptyState+CTA (L157) | ✅ ErrorRetry (L55) | 无分页（全量列表），条目多时一次性渲染 |
| Accounts.tsx | ✅ Skeleton (L121) | ✅ Empty (L157) | ✅ ErrorRetry (L120) | 空态文案误导，见 §5-D |
| Providers.tsx | ✅ Skeleton (L41/129/149) | ✅ Empty | 主数据 ✅ ErrorRetry (L40)；**子区块 ⚠ 邮箱池/代理池失败用 Empty 无重试** (L127/L147) | 见 §1-A |
| Slow.tsx | ✅ Skeleton (L133) | ✅ Empty (L278) | ✅ ErrorRetry (L104) | `enabled=false` 有「已禁用」badge |
| Costs.tsx | ✅ Skeleton (L56) | ✅ Empty（图表区 L189） | 主数据 ✅ ErrorRetry (L55)；**⚠ 预算预测区块无 key 时永久骨架屏** (L362) | 见 §1-B（P1） |
| Logs.tsx | 不适用（WS 流） | ✅ terminal-empty (L284) | ⚠ 断连/鉴权失败有状态徽章+重连按钮 | 见 §2-C 心跳矛盾 |
| Dashboard.tsx | ✅ 卡片 '-' + 图表 fallback | ✅ 路由表/图表空文案 (L258/L455) | 主 stats ✅ ErrorRetry (L71)；**⚠ 5 个独立指标 useApi 失败静默 '-'** | 见 §1-C |
| Security.tsx | ✅ Skeleton (L313) | ✅ Empty (L315) | ⚠ 401 时特判「需管理 Key」横幅+列表区 ErrorRetry (L168) | 处理较完善 |
| Ecosystem.tsx | ✅ Skeleton (L49) | ✅ Empty (L77/L178) | ✅ ErrorRetry (L48) | |
| Gallery.tsx | ✅ Skeleton (L337/410) | ✅ Empty (L432) | ✅ loadError 可见 (L422) | 密码/删除/详情弹窗闭环完整 |
| ChatPlayground.tsx | ✅ ModelPicker loading | ✅ chat-empty (L308) | ✅ 模型目录错误横幅+重试 (L219) | |
| Generate.tsx | ✅ 模型 select loading (L396) | —（无列表） | ✅ 模型加载 ErrorRetry (L264)；生成错误分级 (L21) | |
| Agent.tsx | ✅ Skeleton (L274/294) | ✅ EmptyState (L296/304) | ✅ ErrorRetry (L273/293) | |

### 1-A（P2）Providers.tsx:127-157 邮箱池 / 代理池加载失败无重试动作
`emailApi.error && !emailSources.length ? <Empty text="邮箱池上游加载失败" hint={...}/>` 只给文案不给重试按钮；代理池同（L147）。用户只能整页刷新或等 30s 轮询。主数据走 ErrorRetry，子区块口径不一致。

### 1-B（P1）Costs.tsx:53,362 预算燃烧预测在无管理 Key 时永久骨架屏
- `fetchCostForecast()` 带 `adminHeaders()`（api/misc.ts:231-236），后端 `/v1/cost-forecast` 强制 `check_admin_key`（api/routes/admin/query.py:203）。
- Costs.tsx 只消费 `forecast` 不读 `forecast.error`：无 Key → 401 → `forecast=null` → 恒渲染 `<Skeleton lines={3} height={40} />`（L362）。**无「需管理 Key」提示、无重试按钮、永不停止**，用户判定「功能坏了」。
- 修复方向：对 `forecast.error` 401 渲染引导文案 + 跳安全风控页；或复用 KeyBanner 逻辑降级展示。

### 1-C（P2）Dashboard.tsx:31-46 多个独立指标静默失败
`chatUsage / chatRemaining / authStatus / sseStats / accountPool` 失败仅显示 '-'（L212-232），无降级提示（仅 diag 有 diagError 提示 L235）。SSE 区块无 key 有说明文字（L514）尚可，聊天服务卡片无任何提示。属「看起来有数据其实没拉到」类。

---

## 2. 防重复提交

| 位置 | 提交中态 | 说明 |
|---|---|---|
| Button.tsx:66 `disabled={rest.disabled || loading}` | ✅ 全局统一 loading→disabled | 基建健全 |
| Generate.tsx:423-429 提交按钮 | ✅ loading=running；running 时二次点击提示 (L193) | |
| ChatPlayground.tsx:125 `if(!text||!model||sending) return` + 发送按钮 | ✅ | |
| Security.tsx:59-84 封禁 `if(submitting) return` + disabled | ✅ | 但无二次 confirm，见 §4-B |
| DLQ.tsx:35-39 retry `if(retryingId) return` + disabled | ✅ | |
| Agent.tsx:139-174 planning/submitting | ✅ | |
| Tasks.tsx:231-264 cancel/retry `if(busy) return` | ✅ | |

结论：**防重复提交基建无缺口**（统一 Button loading + 业务 guard 双保险）。

---

## 3. 成功 / 失败反馈闭环

| 操作 | 成功反馈 | 失败反馈 | 列表刷新 |
|---|---|---|---|
| 取消/重试任务 (Tasks.tsx:236-243/257-263) | ✅ toast + 乐观更新 | ✅ toast | ✅ onReload() |
| DLQ 重试 (DLQ.tsx:24-32) | ✅ toast + 乐观移除 | ✅ toast + 回滚 | ✅ reload() |
| DLQ 清空 (DLQ.tsx:46-53) | ✅ toast | ✅ toast | ✅ reload() |
| 封禁/解封 (Security.tsx:76-101) | ✅ toast + aria-live | ✅ toast | ✅ reload() |
| 保存管理 Key (KeyBanner:28-36) | ✅ toast + 广播重连 | —（本地写） | ✅ 广播 |
| Gallery 移除 (Gallery.tsx:317-335) | ✅ toast + 本地移除 | ✅ toast（`ok/ids` 统计） | ✅ 本地过滤 |
| Gallery ZIP (Gallery.tsx:303-314) | ✅ toast | ✅ toast | — |
| DAG 规划/提交/续跑 (Agent.tsx) | ✅ toast | ✅ toast | ✅ reload |
| 聊天发送 (ChatPlayground.tsx:170-181) | ✅ 流式渲染 | ✅ 错误气泡+toast | — |
| **Costs 预算预测** | — | ❌ 401 被吞（见 §1-B） | ❌ |

结论：除 §1-B 外，写操作成功/失败反馈闭环完整。

---

## 4. 危险操作确认

| 操作 | 确认方式 |
|---|---|
| DLQ 清空 (DLQ.tsx:43) | ✅ `confirm()` 原生确认 |
| 解封 IP (Security.tsx:89) | ✅ `confirm()` |
| Gallery 批量移除 (Gallery.tsx:536-553) | ✅ 自定义确认弹窗（软删说明 + loading） |
| **封禁 IP (Security.tsx:59-85)** | ❌ **无二次确认**，点击「⛔ 确认封禁」立即执行。封禁属于高风险写操作，与同页解封（有 confirm）口径不一致 |
| 清空对话 (ChatPlayground.tsx:68-71,296) | ❌ 无确认（本地清空，可接受） |
| 清屏日志 (Logs.tsx:244) | ❌ 无确认（本地清空，可接受） |
| 清除管理 Key (KeyBanner:38-44) | ❌ 无确认（本地操作，可接受） |

缺口：**Security 封禁无二次确认**（P2）。

---

## 5. 权限展示（管理 Key 缺失）

| 位置 | 表现 |
|---|---|
| 全局 KeyBanner (Layout 内 App.tsx:38) | ✅ 未配置常驻黄色提示+输入+跳安全页；已配置绿色小条 |
| Security.tsx:168-174 | ✅ 401 特判「需要管理 Key 才能查看封禁列表」+ Key 输入横幅不被早退隐藏（自举修复良好） |
| DLQ / Tasks 写操作无 Key | ⚠ 点击后 toast 401 错误文案（apiFetch caller 带状态），但无「去配置 Key」引导。KeyBanner 常驻缓解 |
| Costs 预算预测无 Key | ❌ 无任何权限提示，永久骨架（§1-B） |
| Dashboard SSE 指标无 Key | ✅ 有「需配置管理 Key 后加载」空态说明（L514） |
| Logs WS 4401 | ✅ 鉴权失败停重连+「前往配置管理 Key」链接（L188-193/253） |

---

## 6. 路由与深链

- ✅ 404 catch-all（App.tsx:61）；✅ 双层 ErrorBoundary（页面级隔离 L47-59）；✅ 懒加载 Suspense fallback。
- **A（P2）Tasks.tsx:123 空态 CTA 硬编码整页跳转**
  `window.location.hash=''; window.location.pathname='/admin/generate';` 不走 `useNavigate`。路由 basename 由 `import.meta.env.BASE_URL` 决定（App.tsx:33，vite base=`/admin/`）——若部署挂载路径非 `/admin`（或未来改 base）则 404；且整页刷新丢失 SPA 状态、无转场动画。应改用 `useNavigate('/generate')`。
- **B（P2）筛选/分页不持久化、不可深链**：Tasks 状态筛选（Tasks.tsx:22）、Accounts filter/page（Accounts.tsx:97-99）、Security page（Security.tsx:26）刷新后全部复位，无法通过 URL 分享/恢复（未做 query param 同步）。
- ✅ CommandPalette（Cmd+K）用 `navigate`，与侧栏清单同步，无断链。
- Landing：hash 路由 `#/privacy`，`goHome`/`goPrivacy` 一致（App.vue:37-38）。

---

## 7. i18n 缺口（对照 messages.zh.ts，渐进式已知项）

已接入 i18n：Layout 导航、Tasks、DLQ、Gallery 操作条、Dashboard 卡片标题（dash.*）、Generate 主表单（gen.*）、ApiGuide 标题、Agent 部分 key。

**整页硬编码中文未走 i18n**（P2/P3，符合「未接入保持中文」渐进策略，登记待迁移）：
- Accounts.tsx:125,144-147（号池标题/描述）
- Providers.tsx:61-64,74,102,123,143（集群状态/无需账号/需要账号/邮箱池/代理池标题）
- Slow.tsx:120-127（慢请求画像标题/描述/阈值）
- Costs.tsx:102-107 + 全部卡片/表格文案（仅 0 个 cost.* key）
- Health.tsx:142-146 + 评分/维度文案（无 health.* key）
- Ecosystem.tsx:53-56,91-100 + 全页文案（无 eco.* key）
- Security.tsx:140-146,184-190 + 全页文案（无 security.* key）
- Logs.tsx:227-235 + 全页文案（无 logs.* key）
- ChatPlayground.tsx:215-217 + 全页文案（无 chat.* key）
- Dashboard.tsx:205-232,283-349,468-515（聊天服务/服务器配置/路由表/SSE 区块）
- Gallery.tsx:353,372,428,450,546-549（密码/删除/加载更多等弹窗文案）
- Generate.tsx:284,292,312,388（Key 面板/保存/字符计数等）
- Landing PwaInstallPrompt.vue:34（提示文案硬编码中文，无 i18n key）

---

## 8. 分级问题清单

### P1（阻塞体验，1 条）
1. **Costs.tsx:53,362 — 预算燃烧预测无管理 Key 时永久骨架屏**：`forecast.error` 401 被吞，无提示/无重试/永不终止，用户判死功能。根因：前端不读 forecast.error + 后端强制 admin 鉴权（query.py:203）。

### P2（体验缺陷 / 文案误导 / 信息矛盾，9 条）
2. **Security.tsx:59-85 — 封禁 IP 无二次确认**，与同页解封（L89 confirm）口径不一致；封禁属高风险写操作。
3. **Tasks.tsx:123 — 空态 CTA 硬编码 `/admin/generate` 整页跳转**，不走 navigate，base 变化即 404 + 丢 SPA 状态。
4. **Logs.tsx:229-233 — 断连时仍显示「💓 心跳正常」**，与「连接中断」红徽章并存，信息矛盾（lastHeartbeat 不随连接态清零）。
5. **Providers.tsx:127,147 — 邮箱池/代理池加载失败仅 Empty 文案，无重试按钮**（主数据有 ErrorRetry，口径不一致）。
6. **Accounts.tsx:90-91,156-157 — 号池「暂未初始化」文案误导**：`ACTIVE_PROVIDERS` 空 Set 恒空（死代码），但号池已下线却提示「开启 IF_ACCOUNT_AUTO=1 将自动注册」，诱导无效操作。应改为明确的「已下线/只读历史」文案并移除死常量。
7. **Dashboard.tsx:31-46,205-232 — 聊天服务/SSE 等 5 个独立指标失败静默 '-'**，无降级提示（仅 diag 有提示）。
8. **Health.tsx:10-27 — 导出健康报告无 Key 时 401，错误文案「导出失败 HTTP 401」无「需管理 Key」指引**。
9. **Dashboard.tsx:97-107 — 复制管理 Key 无 execCommand 兜底**（HTTP 非安全上下文必失败），与 Feedback.copyToClipboard 不一致。
10. **i18n 缺口（§7 清单）**：13 页中约 9 页主体文案硬编码中文，切换 EN 后大量页面残留中文（渐进式已知，建议登记迁移队列）。

### P3（增强，3 条）
11. **筛选/分页不深链**：Tasks 状态、Accounts filter/page、Security page 刷新即失（§6-B）。
12. **DLQ.tsx 无分页**：全量列表一次性渲染，堆积量大时性能与可用性隐患。
13. **Security.tsx:106-115 查询按钮无 loading/防重复**（GET 幂等，低风险）。

### 已确认无缺陷（免修）
- Button 全局 loading→disabled 防重复（ui/Button.tsx:66）；Tasks/DLQ/Security/Agent 全部写操作均有 loading+guard+toast+reload 闭环。
- Gallery 三态+密码+删除确认弹窗+ZIP+无限滚动，为全站最佳实现。
- DLQ 清空 confirm、解封 confirm、Gallery 删除 confirm 均存在。
- 404 catch-all + 双层 ErrorBoundary + CommandPalette 路由一致，无断链。
- Generate SSE 断线兜底轮询 + 轮询失败上限，无「永久生成中」卡死。

---

*生成：N4 前端 UX 盲点核查（静态阅读，未跑浏览器）· 2026-09-16*
