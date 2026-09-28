# 前后端契约深层审计报告（N1）· contract-audit-v23

- 审计范围：`frontend/src/api/*`（barrel + 7 子域）与 `frontend/src/pages/`、`components/`、`landing/src/` 调用点 ↔ `api/routes/*` + `api/models.py` + `api/handlers.py` + `api/errors.py` + `api/chat_usage.py` + `api/proxy_pool.py` + `api/db/queries.py` 等响应模型。
- 审计方式：静态阅读 + 交叉引用（Windows 下 rg 偶不可用，回退 Grep 工具）。**未运行服务**。
- 结论置信：所有字段比对均为「实际文件读取」所得；无法运行的服务交互行为标 `待验证`。
- 日期：2026-09（v23 审计批次）

---

## 0. 总览

| 项 | 结果 |
|---|---|
| 端点总数（前端实际调用） | ~50 个（管理面板 45 + landing 12，去重后约 50） |
| 完全对齐 ✅ | 44 |
| 存在偏差 ⚠️ | 5（鉴权缺参 / 重试字段错配 / 状态伪造 / WS 闭包码过时 / 错误码未消费） |
| 完全断裂 ❌ | 0 |
| P0 | 0 |
| P1 | 2 |
| P2 | 8 |
| P3（记录不占交付） | 2 |

---

## 1. 端点清单（前端调用 → 后端路由 → 对齐状态）

### 1.1 管理面板（frontend/src/api → api/routes）

| 前端调用 | 方法+路径（含 query/body） | 后端路由 | 对齐 |
|---|---|---|---|
| `fetchTasks` | GET `/v1/tasks?limit&offset&status` | tasks.py:20 `list_tasks` | ✅ |
| `fetchTask` | GET `/v1/tasks/{id}` | tasks.py:44 `get_task` | ✅ |
| `fetchEditTask` | GET `/v1/edit/tasks/{job_id}` | generate.py:95 | ✅ |
| `cancelTask` | POST `/v1/tasks/{id}/cancel` | tasks.py:52 | ✅ |
| `retryTask` | POST `/v1/tasks/{id}/retry` | tasks.py:72 | ✅ |
| `fetchDLQ` | GET `/v1/dead-letter-queue` | admin/write.py:19 | ✅ |
| `retryDLQTask` | POST `/v1/dead-letter-queue/{id}/retry` | admin/write.py:26 | ✅ |
| `clearDLQ` | DELETE `/v1/dead-letter-queue` | admin/write.py:44 | ✅ |
| `generateImage` | POST `/v1/generate` body{prompt,aspect_ratio,model,resolution,download} | generate.py:68 `GenerateRequest`(models.py:10) | ✅ |
| `editImage` | POST `/v1/edit` body{image?,images,prompt,model,download} | generate.py:89 `EditRequest`(models.py:34) | ✅ |
| `chatCompletions` | POST `/v1/chat/completions` | chat.py:563 | ✅ |
| `fetchChatUsage` | GET `/v1/chat/usage?period=1h/24h/7d/30d` | chat.py:770 | ✅ |
| `fetchChatRemaining` | GET `/v1/chat/remaining` | chat.py:834 → chat_usage.remaining_credits(:303) | ✅ |
| `fetchChatAuthStatus` | GET `/v1/chat/auth/status` | chat.py:802 | ✅ |
| `fetchChatModels` | GET `/v1/chat/models` | chat.py:792 | ✅ |
| `fetchProviders` | GET `/v1/providers` | admin/query.py:79 | ✅ |
| `fetchEmailSources` | GET `/v1/email-sources` | admin/query.py:532 | ✅ |
| `fetchRoutingRecords` | GET `/v1/routing/records?limit` | admin/query.py:573 | ✅ |
| `fetchProxyPool` | GET `/v1/proxy-pool?page&page_size` | admin/query.py:511 → proxy_pool.snapshot(:279) | ✅ |
| `fetchImageModels` | GET `/v1/models` | admin/query.py:38 | ✅ |
| `fetchStats` | GET `/v1/stats` | admin/query.py:275 | ✅ |
| `fetchDiagnostics` | GET `/v1/diagnostics` | admin/query.py:666 | ✅ |
| `fetchCost` | GET `/v1/cost` | admin/query.py:107 | ✅ |
| `fetchCostForecast` | GET `/v1/cost-forecast`（adminKey） | admin/query.py:192 | ✅ |
| `fetchAccountPool` | GET `/v1/account-pool?page&page_size&search` | admin/query.py:214 | ✅ |
| `fetchSystemSpec` | GET `/v1/system` | health.py:229 | ✅ |
| `fetchAiEcosystem` | GET `/v1/ai-ecosystem` | ecosystem.py:204 | ✅ |
| `fetchSseStats` | GET `/v1/sse/stats`（adminKey） | admin/query.py:723 | ✅ |
| `fetchLogs` | GET `/v1/logs?lines` | admin/query.py:490 | ✅（注释过时，见 F4） |
| `blockIp` | POST `/v1/admin/security/block-ip` | security.py:53 | ✅ |
| `unblockIp` | DELETE `/v1/admin/security/unblock-ip?ip` | security.py:106 | ✅ |
| `fetchBlocklist` | GET `/v1/admin/security/blocklist?page&page_size|limit` | security.py:119 | ✅ |
| `fetchBlockStatus` | GET `/v1/admin/security/status?ip` | security.py:157 | ✅ |
| `fetchGallery` | GET `/v1/gallery?limit&password` | admin/query.py:357 | ✅（旧兼容路径） |
| `fetchGalleryPage` | GET `/v1/gallery?page&page_size&status&model&search&password` | admin/query.py:357 | ✅ |
| `fetchGalleryDetail` | GET `/v1/gallery/{id}?top_k&password` | gallery.py:193 | ✅ |
| `downloadGalleryZip` | POST `/v1/gallery/zip?password` body{task_ids}，读 `X-Total` 头 | gallery.py:233 | ✅ |
| `softDeleteGalleryItem` | DELETE `/v1/gallery/{id}?password` | gallery.py:322 | ⚠️ **F1（P1）缺管理 Key** |
| `signGallery` | GET `/v1/gallery/sign?limit`（adminKey） | admin/query.py:406 | ✅ |
| `runDag` | POST `/v1/agent/dag/run` | agent_dag.py:133 | ✅ |
| `getDagRun` | GET `/v1/agent/dag/{run_id}` | agent_dag.py:280 | ✅ |
| `resumeDag` | POST `/v1/agent/dag/{id}/resume` | agent_dag.py:326 | ✅ |
| `listDagRuns` | GET `/v1/agent/dag?limit&status` | agent_dag.py:257 | ✅ |
| `planDag` | POST `/v1/agent/dag/plan` | agent_dag.py:387 | ✅ |
| `saveSkillFromRun` | POST `/v1/agent/skills/save-from-run` | agent_skills_admin.py | ✅ |
| `mySkills` | GET `/v1/agent/my-skills` | agent_skills_admin.py | ✅ |

### 1.2 landing（Vue3 门户）

| 前端调用 | 路径 | 后端路由 | 对齐 |
|---|---|---|---|
| usePolling | GET `/v1/stats` `/v1/providers` `/v1/models` `/v1/meta` `/v1/chat/usage?period=24h` `/v1/gallery?limit=N` `/v1/healthz` | 对应上表 | ✅ |
| PortalChat/HomeChat | GET `/v1/chat/models` POST `/v1/chat/completions` | chat.py | ✅ |
| PortalGenerate | POST `/v1/generate` `/v1/edit` GET `/v1/tasks/{id}` `/v1/edit/tasks/{id}` + EventSource `/v1/tasks/{id}/events` | generate.py / tasks.py | ✅ |
| PortalAgent | POST `/v1/agent/dag/plan` `/v1/agent/dag/run` GET `/v1/agent/dag/{id}` | agent_dag.py | ✅ |
| PortalPpt | POST `/v1/skills/ppt/generate` body{title,pages:[{headline,points:[]}]} | ppt.py:35（PptOutline pages:list[dict]，generate_pptx:37 迭代 points[:5]） | ✅ |
| PortalVideo | GET `/v1/meta`（video_enabled 探测） | health.py:247 | ✅（未直接调 /v1/video） |

> 注：`/v1/video`、`/v1/generate/async`、`/v1/events/tasks`、`/v1/logs/ws`、`/v1/admin/health-report` 后端存在，前端有消费者（/v1/video 暂无 UI 消费者，仅 meta 探测）。

---

## 2. GET 列表端点分页协议一致性

| 端点 | 前端参数（默认） | 后端参数（默认/约束） | 一致 |
|---|---|---|---|
| `/v1/tasks` | `limit`(50)/`offset`(未传=0)/`status` | `limit`=50 ge=1 le=200 / `offset`=0 ge=0 / `status` / `model` / `sort` | ✅ 命名一致；前端未用 model/sort（后端可选） |
| `/v1/gallery` | `page`(1)/`page_size`(24)/`status`/`model`/`search`/`password` | `limit`=GALLERY_LIMIT(50)/`page`=1/`page_size`(缺省=limit 兼容)/`status`/`model`/`search` | ✅ 响应 `{items,total,page,page_size,count}`，前端消费 `items/total` |
| `/v1/proxy-pool` | `page`(1)/`page_size`(20) | `page`=1/`page_size`=20 le=500 | ✅ 响应 `{total,page,page_size,total_pages,items,top,...}` 全对齐 |
| `/v1/account-pool` | `page`(1)/`page_size`(20)/`search` | `page`=1/`page_size`=20 le=100/`search` | ✅ 响应 `{items,items_total,page,page_size,total_pages}` 前端消费一致 |
| `/v1/admin/security/blocklist` | `page`(1)/`page_size`(100) 或 `limit` | `page`=1/`page_size`=100 le=1000 + `limit` 旧语义 | ✅ 响应 `{items,count,total,page,page_size,has_more}` 全对齐 |
| `/v1/agent/dag` | `limit`(10)/`status` | `limit`=20 le=100/`status` | ✅ 响应 `{items,count}` |
| `/v1/dead-letter-queue` | 无分页 | `limit`=20 le=100 | ✅ 响应 `{items,count}` |
| `/v1/logs` | `lines`(100) | `lines`=50 ge=1 le=200 | ✅（前端传 100 在界内） |
| `/v1/slow` | `limit`(50) | `limit`=50 ge=1 le=500 | ✅ |
| `/v1/routing/records` | `limit`(50) | `limit`=50 le=200 | ✅ |

**结论：分页协议无命名冲突。** 唯一语义差异：`/v1/gallery` 的 `page_size` 默认值与 `limit` 联动（缺省时 `page_size=limit`），前端显式传 `page_size` 时不受影响（已验证 Gallery.tsx 显式传 24）。

---

## 3. 状态枚举逐字比对

### 3.1 任务顶层状态（GET /v1/tasks 轮询 + task_to_public.status）

| 前端引用 | 值 | 后端来源 | 一致 |
|---|---|---|---|
| useTaskProgress.ts:40 `TERMINAL` | `completed`/`error`/`cancelled` | queries.py mark_finished(completed/error)、queries.py:391 cancelled | ✅ |
| Tasks.tsx:39-50 getStatusBadge | `completed`/`processing`/`error`/`pending`/`cancelled` | DB 实际写入值 | ✅ |
| Tasks.tsx:76-82 筛选 option | `pending`/`processing`/`completed`/`error`/`cancelled`/`archived` | list_tasks 支持（archived 为冷归档态，queries.py:266 排除逻辑+显式可查） | ✅ |
| Generate.tsx:107/171 | `completed`/`error` 终态判断 | 同 | ✅ |

DB 实际状态全集（queries.py:113/122/174/377/391 + queue_store.py:71）：`pending` / `processing` / `completed` / `error` / `cancelled` / `deleted`（软删）/ `archived`（冷归档）。

### 3.2 阶段徽章 status_detail（SSE 事件负载 + 前端映射兜底）

| 前端值（TaskProgress.tsx:28-34 / useTaskProgress.ts:43-56） | 后端 SSE 来源 | 一致 |
|---|---|---|
| `queued` progress=5 | engine.py:193-204 入队事件 `status_detail:"queued"` progress=5 | ✅ |
| `solving` progress=30 | engine.py:535-547 处理阶段 `status_detail:"solving"` progress=30 | ✅ |
| `generating` progress=80 | engine.py:600-611 `status_detail:"generating"` progress=80（**事件无顶层 status 键**，前端 `data.status ?? 'processing'` 兜底 ✅） | ✅ |
| `completed`/`cancelled` progress=100 | dispatch.py:160-165 `_terminal_detail` 映射 + progress=100 | ✅ |
| `error`（终态无徽章） | dispatch.py:160 `get(status, status)` → "error"，前端 finish() 对 error 置 statusDetail=null | ✅ |

**前端已知缺口且已兜底**：GET 轮询（task_to_public）不含 `status_detail`/`progress`（queries.py:863-880 无此二字段；TaskInfo 模型 models.py:50-66 亦无）——前端 useTaskProgress 注释明确声明该事实并做 pending→queued/5 映射，非缺陷。

### 3.3 DAG 状态（agent/dag.py）

| 前端（DagGraph STATUS_META / agent.ts） | 后端 | 一致 |
|---|---|---|
| node: `pending`/`running`/`succeeded`/`failed`/`skipped` | dag.py:117-134 public_state；dag.py:405 skipped | ✅ |
| run: `pending`/`running`/`succeeded`/`failed` | dag.py:145/158/160-166 | ✅ |
| run.status 含 `skipped`？ | mark_finished 里 skipped 视为合法终态（注释 dag.py:161-162），但 run 级 status 仅四态 | ✅ 前端 run 类型声明 `'pending'|'running'|'succeeded'|'failed'` 未含 skipped，与后端一致 |

### 3.4 其他

| 域 | 前端 | 后端 | 一致 |
|---|---|---|---|
| Skill status | `draft`/`approved`/`rejected` | agent_skills_admin.py | ✅ |
| 视频状态（无前端 UI 消费者） | — | video.py:81 `queued`/`rendering`/`completed` | 待验证（无消费者） |
| AccountPoolItem.status | 字符串透传 | account_pool（ok/dead/banned/cooling/exhausted 等） | ✅ 透传 |
| `error` 终态的双写法 | 前端仅用 `error` | 任务表历史存在 `error`（写库统一 error；tasks.py:86 retry 允许 `("error","failed","cancelled")`——兼容历史 failed） | ✅ 前端用 error 判断正确；`failed` 仅出现在 retry 校验与 DAG，任务列表不会返回 failed |

### 3.5 ⚠️ 状态伪造散点（F5，P2）

后端 **4 处**返回 `"status": "queued"`，而 DB/任务列表从未有 `queued` 状态：
- generate.py:77 `body["status"] = "queued"`（202 轮询中响应）
- mcp/tools.py:148、video.py:76、tasks.py:103（retry 返回 `"status":"queued"`）

前端 Task 接口（tasks.ts:22-27）未声明 `queued`；useTaskProgress TERMINAL（useTaskProgress.ts:40）不含 queued → 轮询继续，行为正确。但「DB 契约 `pending` ↔ 响应伪造 `queued`」语义不一致，调用方按状态做筛选/统计时（如状态过滤 UI）无法把 queued 纳入 pending 筛选（Tasks.tsx 筛选只有 pending，无 queued——所以 retry 后返回 queued 的任务若立即被状态筛选 pending 过滤会消失）。

---

## 4. 错误码 / HTTP 状态分布比对

### 4.1 后端错误响应形状

- `error_response`（errors.py:211-236）：`{"error": {code, message, details}}`
- `app_error_handler`（handlers.py:27-47）：429 时 error 顶层追加 `retry_after_seconds` + `human_hint`，并设 `Retry-After` 响应头
- 422 请求校验：委托 FastAPI 默认处理器，返回 `{detail: [...]}`（handlers.py:73-83）
- 错误码体系（errors.py:31-56）：`AUTH.001/002/003`、`VAL.001-004`、`PROV.001-003`、`SYS.001-006`、`RATE.001`

### 4.2 前端解析路径

| 前端 | 解析逻辑 | 后端实际 | 一致 |
|---|---|---|---|
| apiFetch readErrorBody（core.ts:80-104） | `code`=body.code??body.error.code；`message`=body.detail??body.message??errObj.message??body.error；`retry`=errObj.retry_after_seconds??body.retry_after_seconds??errDetails.retry_after_seconds | error 对象在 `body.error` 内，message 在 `errObj.message`，retry 在 error 顶层 ✅ | ✅ |
| apiFetch 429 分支（core.ts:162-169） | 有秒数或 code===`RATE.001` → Toast「太快啦」；否则用 detail | 429 响应 error 顶层带 retry_after_seconds ✅ | ✅ |
| `STATUS_TEXT`（core.ts:42-60） | 状态码兜底中文 | 覆盖 400-504 | ✅ |
| chat-utils.getErrorPayload（chat-utils.ts:156-181） | `status===429/401` 分支 + message 子串；`retry`=body.retryAfterMinutes??retry_after_minutes??nested… | 见 F2 ⚠️ | ⚠️ **F2（P1）retry 字段名错配** |
| classifyError（Feedback.tsx:62-74） | 子串匹配 429/401/502/503/504/provider down | 不读 `error.code` | ⚠️ **F6（P2）** |

### 4.3 鉴权状态码分布

| 场景 | 后端 | 前端分支 | 一致 |
|---|---|---|---|
| 聊天无 Key / Key 无效 | 401（chat.py:563 guard_chat_request → auth.py） | chat-utils 401 → Key 提示 | ✅ |
| 管理写操作无 Key | 401/403（auth.py:130-158 check_admin_key） | Security.tsx:168 401/403 → Key 横幅 | ✅ |
| 画廊密码错/签名过期 | 403（admin/query.py:329-333 _gallery_auth） | Gallery.tsx:146 403 → 密码框 | ✅ |
| 画廊软删无管理 Key | 401/403 | Gallery.tsx 删除 catch 吞掉 | ⚠️ **F1** |
| DLQ/导出/健康报告无管理 Key | 401/403 | 页面自带 adminHeaders | ✅ |
| SSE 连接超限 | 429（sse_events.py:216） | 前端 EventSource onerror 兜底轮询 | ✅ |
| 日志 WS 鉴权 | 已开放（不再 401/4401） | 前端仍监听 4401 | ⚠️ **F3（P2）** |

---

## 5. 时间格式比对

| 字段 | 后端返回 | 前端消费 | 一致 |
|---|---|---|---|
| Task.created_at / duration_sec | SQLite REAL 秒（Unix epoch）；task_to_public 透传 | `new Date(created_at * 1000)`（Tasks.tsx:303） | ✅ |
| DAG run/node created_at/finished_at | `round(ts, 3)` 秒（dag.py:131-133/177-178） | `new Date(ts * 1000)`（Agent.tsx fmtTime:24-27） | ✅ |
| SlowItem.created_at | `s.created_at` 秒 | `new Date(ts * 1000)`（Slow.tsx:93-96） | ✅ |
| LogEntry.timestamp | `_formatter.formatTime(record)` → `"YYYY-MM-DD HH:MM:SS"` 本地时间字符串（log_buffer.py:51） | Logs 直接展示字符串（Logs.tsx LogEntry.timestamp: string） | ✅ |
| Diagnostics.timestamp | `int(time.time())` 秒 | Dashboard 未直接格式化（仅透传） | ✅ |
| stats.daily[].day / monthly[].month | `"YYYY-MM-DD"` / `"YYYY-MM"`（queries.py stats_daily/stats_monthly） | 图表直接消费字符串 | ✅ |
| AccountPoolItem.created_at/checkin_at/next_claim_at | 秒（或 null） | Accounts.tsx 透传/格式化 | ✅（未发现 *1000 错配） |
| CostForecast.projected_exceed_date | `"YYYY-MM-DD"` 字符串（cost_forecast.py） | Costs.tsx 直接展示 | ✅ |
| SSE 事件 `ts` | 秒浮点 | 前端未消费（事件 id 用于 Last-Event-ID） | ✅ |

**结论：时间单位全链路统一为「Unix 秒（浮点）」，日志与日历字段用 ISO 风格字符串，无毫秒/时区混用。**

---

## 6. 发现清单（按严重度）

### F1 ⚠️ P1 — 画廊软删缺管理 Key，删除功能在配置了 Key 的环境必然 401/403
- **前端**：`frontend/src/api/misc.ts:111-117` `softDeleteGalleryItem(taskId, password?)` 仅拼 `password` query，无 `adminHeaders()`/Authorization。
- **后端**：`api/routes/gallery.py:322-345` DELETE `/v1/gallery/{task_id}` 在 `_gallery_auth(password)` 后调用 `check_admin_key(request, scope="gallery-delete")`。
- **后端**：`api/auth.py:130-158` check_admin_key：配置了 `IF_ADMIN_KEYS`/`IF_API_KEYS` → 401/403；均未配且 `IF_ADMIN_KEY_OPEN=0` → 403 拒绝。
- **调用点**：`frontend/src/components/Gallery.tsx:317-335` `handleDelete` 传入 `effectivePwd`（画廊访问密码），从不携带管理 Key。
- **影响**：默认/生产 Key 配置下删除 401/403，异常被 catch 吞掉（Gallery.tsx:326），本地乐观移除（:329）后刷新即恢复；Toast「已移除 0/N 张」。功能名义存在、实际不可用。
- **复现**：配置任一 Key → 画廊选中删除 → 后端 401/403。
- **修复方向（待授权，仅建议）**：`softDeleteGalleryItem` 合并 `...adminHeaders()`；或后端把「画廊删除」降为画廊访问权限组内（删除管理员既有的写组策略，需评估）。

### F2 ⚠️ P1 — chat-utils 重试等待字段名与后端错配（seconds vs minutes）
- **前端**：`frontend/src/components/chat/chat-utils.ts:176` 只读 `retryAfterMinutes`/`retry_after_minutes`。
- **后端**：`api/handlers.py:41-45` 429 时 error 顶层只放 `retry_after_seconds`（秒）；`errors.py` 无 `retry_after_minutes`。
- **消费点**：`frontend/src/pages/ChatPlayground.tsx:177-178` `retryAfterMinutes !== undefined` 才显示「请约 N 分钟后重试」。
- **影响**：聊天 429 的倒计时提示永不显示（message 被替换为「繁忙切换」通用文案，功能可用但提示缺失）。
- **修复方向（仅建议）**：chat-utils 改为读 `retry_after_seconds`（秒 → 分钟换算），或读 ApiError 层已解析的 `retryAfterSeconds`。

### F3 P2 — 日志 WS「4401 鉴权失败闭包码」契约已消失，前端死分支 + 防御失能
- **前端**：`frontend/src/pages/Logs.tsx:16-18`（注释）+ `:71-76` `event.code === AUTH_FAILED_CODE(4401) → 停止重连`。
- **后端**：`api/routes/admin/query.py:496-508` `log_websocket` v7.7.8 起公益开放，无鉴权、不发送 4401；`api/log_ws.py:43-50` 仅 register/unregister。
- **影响**：4401 永不触发（当前无害）；若未来后端恢复鉴权，旧前端将无限重连直到 MAX_RECONNECTS=8 熔断（防御实际失能）。注释与行为不一致，误导维护。
- **另注**：`frontend/src/api/misc.ts:138-141` fetchLogs 注释「须携带管理 Key」与后端开放现状不符（P2，仅注释）。

### F4 P2 — fetchGallery(limit) 旧调用仅存于测试，组件已迁移但 api 层保留双签名
- `frontend/src/api/misc.ts:56-60` fetchGallery 与 `fetchGalleryPage` 并存；`frontend/src/test/api.test.ts:400-410` 仍单测旧签名；`frontend/src/components/Gallery.tsx:8` 注释确认组件已迁移。
- 后端 `gallery:{}` 缓存与 `limit` 兼容逻辑（admin/query.py:357-403）仍保留双语义，无破坏。P2（死代码/注释级）。

### F5 P2 — 后端 4 处伪造 `status:"queued"`，与 DB 契约 `pending` 不一致
- `api/routes/generate.py:77`（202 轮询中）、`api/mcp/tools.py:148`、`api/routes/video.py:76`、`api/routes/tasks.py:103`（retry 响应）。
- 前端 tasks.ts Task.status 未声明 queued；useTaskProgress 对 queued 非终态继续轮询（行为正确）。
- **影响**：按 `status` 过滤的调用方（Tasks.tsx:76-82 只有 pending，无 queued）无法把 retry 后返回 queued 的任务归入「排队」筛选；语义文档化应统一为 `pending`。

### F6 P2 — 聊天/生图错误分类依赖「HTTP 状态 + 消息子串」，未消费后端分层错误码
- `frontend/src/components/Feedback.tsx:62-74` classifyError、`chat-utils.ts:170-174` getErrorPayload 用 `status` + 子串（`rate/limit/限流`、`key/auth/unauthorized`）。
- 后端已建立 `AUTH.001 / RATE.001 / PROV.001 / SYS.002` 分层码（errors.py:31-56），且 `/v1/chat/completions` 由前端直接 fetch（chat.ts:105-112）不经 apiFetch，`error.code` 未被读取。
- 影响：message 语言切换（en）时子串误判；`VAL.001`/`SYS.003` 归 generic，无备用引擎提示。韧性可接受，但错误码体系未在聊天路径生效。

### F7 P2 — 429 语义差异：前端 429 Toast 阈值与后端 429 分布
- 后端 429 用于 `RATE.001`（真限流）、`SYS.002`（QUEUE_FULL 队列满）、`PROV.002`（额度耗尽）、SSE 连接数超限。
- 前端 core.ts:162-169 仅当 `retryAfterSeconds` 或 `code===RATE.001` 弹「太快啦」；QUEUE_FULL（code=SYS.002）无秒数时走 detail Toast —— 与后端语义一致（已按 L2 修复区分），**无实质错配**。列为记录（P3 观察项），不占交付。

### F8 P2 — /v1/gallery/sign 签名 URL 与前端解析的双格式约定依赖注释
- 后端签发 `password=<exp>:<sig>` 紧凑 token（admin/query.py:336-342 `_gallery_signed_url`）。
- 前端 Gallery.tsx:26-55 `extractExp`/`extractPassword` 同时解析 `?exp=`（单图 URL 预留）与 `password=exp:sig`。
- 后端当前仅签名「列表 URL」（/v1/gallery?limit&password），单图 image_url 无签名（Gallery.tsx:202-204 注释明示）；`?exp=` 解析为预留分支，无实际数据源。P2（兼容性冗余，无 break）。

### F9 P2 — Logs 页 WebSocket URL 携带 `?api_key=`（弱安全通道）
- `frontend/src/pages/Logs.tsx:50-53` 带管理 Key 时拼 `?api_key=` 到 WS URL。
- `api/auth.py:102-108` `_extract_key` 明确将 query 传 Key 标记为弱安全通道（记录 warning）；当前后端 WS 已开放，Key 仅透传不校验。
- 影响：当前无害（不校验），但浏览器历史/代理日志可能残留 Key；且后端不再消费该参数。P2。

### F10 P3 — Slow.tsx 直接 fetch 未经 apiFetch 封装
- `frontend/src/pages/Slow.tsx:40-47` 本地 `fetchSlow`：非 2xx 读 text 抛错，无统一超时/ApiError/Toast。
- 与其余页面（apiFetch 封装）不一致；字段契约本身对齐。P3。

### F11 P3 — /v1/logs lines 上限 200 vs 前端默认 100（注释 `le=200`）
- 后端 `lines: int = Query(50, ge=1, le=200)`（admin/query.py:491）与前端 `fetchLogs(lines=100)` 在界内，无冲突。P3 记录项。

---

## 7. 结论

- **P0：无。**
- **P1（2 项，建议本轮修复）**：F1 画廊软删缺管理 Key（功能不可用）；F2 聊天 429 重试提示字段错配。
- **P2（8 项，核心闭环后处理）**：F3-F9。
- **P3（2 项，记录）**：F10/F11。
- 分页协议、状态枚举（含 SSE 阶段徽章）、时间格式三块**全部逐字对齐**，无命名/单位冲突。
- 主要系统性风险集中在**鉴权边界传递**（F1）与**聊天错误码消费**（F2/F6），其余为注释过时/死分支/契约文档化问题。
