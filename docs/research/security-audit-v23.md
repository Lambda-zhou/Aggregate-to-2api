# 安全纵深审计报告（v23，N3 节点）

- 审计视角：最倒霉的调用接入方 + 最狠的反对者（攻防双视角）
- 方法：静态阅读 + 全局检索（内置 rg）；**未运行服务、未修改任何文件**
- 审计时间：2026-09-28
- 结论速览：无 P0（无被无条件利用的远程 RCE / 无未鉴权全站写）。**P1 共 5 项**（其中 2 项为反代部署配置依赖、1 项为日志脱敏通道缺失、1 项为未鉴权写、1 项为成本面），剩余为 P2/P3。

---

## 1. 限流绕过面

### 1.1 X-Forwarded-For 伪造（生图主链路：机制已加固，配置依赖突出）

`request_guard.get_client_ip`（`api/request_guard.py:346-373`）仅在「对端命中 `IF_TRUSTED_PROXIES`」时才解析 XFF 且取**最右段**（追加语义，客户端无法伪造）；对端不受信一律用 socket 对端。机制正确。

**问题出在配置依赖**：`IF_TRUSTED_PROXIES` 默认仅 `127.0.0.1,::1`（`api/request_guard.py:182-187`、`api/config/security.py:15`）。一旦生产部署在 Nginx/CF 反代后（非本机 IP，如 docker 网关 `172.28.0.1` 或云上反代），**所有用户 socket 对端都是反代 IP**：

- 生图限流/封禁/白名单全部塌缩为「反代 IP 单桶」：单客户端可耗尽全站 `IF_REQUESTS_PER_MINUTE`（默认 10/分钟）额度（精确 DoS）；
- **自动封禁放大器**：`_record_auto_block_violation`（`request_guard.py:488-524`，`IF_AUTO_BLOCK_ENABLED` 默认 True、阈值 3 次/300s）对「反代 IP」记违规——任一用户超限即可能把反代 IP 拉黑 → **全站 403**（`block_type="block"` 走 `AppError FORBIDDEN`，`request_guard.py:555-559`）。

生产模板 `${deploy/.env.production.example:89}` 已配 `IF_TRUSTED_PROXIES=127.0.0.1,::1,172.28.0.1`，故属**上线前必须核对**的门禁项（若反代 IP 不在列表，效果等同把 Web 服务性 DDoS 开关交给任意三连刷的客户端）。

### 1.2 聊天 / DAG / 视频 / PPT 限流 key 未走受信代理解析（P1）

`api/auth.py:186-187`（`check_chat_rate_limit`）与 `:223-224`（`check_dag_rate_limit`）直接用 `request.client.host`（socket 对端），**未调用** `get_client_ip`；`guard_chat_request` 被 `/v1/chat/completions`（`chat.py:565`）、`/v1/messages`（`:611`）、`/v1/video`（`video.py:43,84`）、`/v1/skills/ppt/generate`（`ppt.py:40`）、`/v1/mcp`（`mcp/server.py:201,329`）、DAG 与 agent 端点（`agent_dag.py:137,338,392`、`agent/routes.py:101,124,151`）广泛复用。

后果（反代部署下与 1.1 同根但更广）：聊天（默认 60/min）、DAG（默认 30/min）等限流全部退化为**全站单桶共享**——单客户端或小团体可在 1 分钟内耗尽全站聊天/DAG 配额（对公益端点做服务性拒绝），且无法按真实用户精细限流。防御方差于「生图已加固、聊天未加固」的不一致。

### 1.3 滑动窗口边界（机制正确）

`request_guard.py` L1 秒级令牌桶（`:247-286`）+ L3 精确滑动窗口（`:623-647`）+ 每日窗口（`:560-571`），时间基统一墙上时钟（S1 修复，见文件头注释），窗口用 `[now-60, now]` 剪裁列表，**无**固定窗口双倍刷漏。redis 模式走 ZSET+Lua 原子滑窗（`:600-621`），异常降级内存继续限流（不 fail-open）。

### 1.4 多限流 key / 白名单绕过面

- **per-IP 限流换 IP 即绕过**：全部限流均以 IP 为唯一维度，无行为/设备/指纹维度。攻击者换代理/拨号即可绕过 10/min 与 60/min —— 对「上限额消耗类」（见 2.3 DAG/agent）是放大因子。属 per-IP 模型的固有留白（P3）。
- **白名单旁路**：`IF_IP_WHITELIST` 命中直接 `return`（`request_guard.py:534-535`）。若生产配了宽泛白名单或未加反代段，则白名单与 1.1 叠加可让整段 IP 免流。
- `_ip_locks`/播桶 dict 仅 >10000 条才 GC（`:651-680`）；唯一 IP 海量变化时内存有界增长（P3，非绕过）。

---

## 2. 鉴权缺口

### 2.1 `POST /v1/tasks/{task_id}/cancel` 无任何鉴权/限流（P1）

`api/routes/tasks.py:52-69`：仅检查 `IF_TASK_CANCEL_ENABLED` 开关，**无 `guard`、无 Key、无 IP 限流**。任何人只要拿到 task_id（UUID4，不可枚举，但**可从公开画廊列表/公开任务列表获得已完成/进行中任务 id**：`/v1/gallery`、`/v1/tasks` 均匿名公开，见 3.5），即可取消任意任务 → 破坏他人生成结果（依赖型 DoS）。对比同文件 `POST /v1/tasks/{task_id}/retry`（`:72-103`）走了 `_prepare`（guard + 限流）——cancel 是漏网的写操作。

### 2.2 `POST /v1/tasks/{task_id}/retry` 无 Key 可复制任意他人任务重跑（P2）

`tasks.py:72-103`：经 `_prepare` 限流，但无 Key；攻击者可枚举公开画廊 id，对任意已完成任务无限「重试」→ 免费上游额度重复消耗（与 2.3 同源）。

### 2.3 DAG / agent 全链无 Key 公益开放 + 真实执行（P1，成本/额度面）

- `POST /v1/agent/dag/run`（`agent_dag.py:133-193`）与 `resume`（`:326-384`）、`plan`（`:387-405`）仅 `guard_chat_request`（per-IP 30/min），**无 Key**。
- `IF_MOCK_UPSTREAM` 默认 **False**（`api/config/__init__.py:299`、`:722-723`）→ 生产 `llm` 节点真实调 tryingopen（`agent_dag_exec.py:129-175`）、`image` 节点真实调图像 provider（`:310-347`）。
- 预算门禁**默认 off**（`if_budget_guard_mode` 默认 `"off"`，`config/__init__.py:319`）且免费上游估算恒 0（`api/agent/budget_guard.py:29-35` imagefree/aifreeforever/tryingopen = 0.0）→ enforce 对免费上游永不拦截。
- 结合 1.4「换 IP 绕过 per-IP」：攻击者可无限刷 tryingopen 免费 LLM 额度与 imagefree 生图额度直至上游配额耗尽（**影响全站可用性**）。项目定位「公益开放」故不强制 Key，但至少应把 DAG/agent 的限流下限收紧或要求弱 Key（P1→P2 之间，按成本面定 P1）。

### 2.4 管理端点鉴权覆盖核对（总体达标 + 两处漏网读端点）

管理写操作（封禁/解封 `security.py:53,106`、DLQ 重试/清空 `write.py:29,47`、画廊删 `gallery.py:334`、导出 `query.py:801`、审计 `query.py:635`、SSE 统计 `query.py:726`、成本预测 `query.py:203`、健康报告 `query.py:874`、技能审批 `agent_skills_admin.py:109/119/129`、human_inbox 决策/导出 `agent_human.py:54/71`、MCP 暴露 `mcp_admin.py:25/52/64`、priority=0 `generate.py:46`）全部 `check_admin_key` ✓。

漏网（只读但敏感，见 3.x 信息泄露）：
- `GET /v1/dead-letter-queue`（`write.py:19-23`）**无 Key**，DLQ 任务明细公开；
- `POST /v1/errors/frontend`（`query.py:451-470`）匿名可写（刷假错误/污染前端错误聚合，无任何幂等/校验）；
- `GET /v1/logs`、`WS /v1/logs/ws`（`query.py:490-508`）匿名公开（内容见 3.1/3.2）。

### 2.5 管理 Key 未配置时继承业务 Key（P2）

`auth._admin_keys`（`api/auth.py:47-56`）：`IF_ADMIN_KEYS` 为空时**继承 `IF_API_KEYS` 业务 Key 池**。单 Key 职责混合：业务 Key 一旦泄露（经弱通道 `?api_key=` 或客户端），直接获得封禁他人/清空 DLQ/导出全量任务（含 client_ip）/读审计 的权限。生产模板已强制分离（`deploy/.env.production.example:24-31`），属**上线核对项**。

### 2.6 画廊鉴权（P2）

`_gallery_auth`（`query.py:323-333`）：签名 URL → 静态密码 → **皆空即开放**（向后兼容）。`IF_GALLERY_PASSWORD`/`IF_GALLERY_SIGNING_SECRET` 默认空（`config/__init__.py:195-198`）→ 生产未配置时画廊（含用户 prompt 与生成图）全公开；`POST /v1/gallery/zip` 也仅凭读组密码即可打包他人作品（`gallery.py:233-245`）。v16 后删除已加 `check_admin_key`（写读分离 ✓），但**读组本身仍可凭画密码出来**。

---

## 3. 敏感泄漏

### 3.1 `/v1/logs/ws` WebSocket 广播**未脱敏**（P1，本报告最关键发现）

- `api/log_ws.py:56-68` `broadcast_log` 直接把 `record.getMessage()` 广播给所有 WS 订阅者——**未复用** `log_buffer._redact` 脱敏（v20.3.1 P1 只修了 `log_buffer.py:48-57` 的内存缓冲通道，漏掉本通道）。
- `api/lifespan.py:41,48`：`ws_log_handler` 注入 root logger 及 `imagefree_api/dispatch/worker/routes/uvicorn` 等 logger → 所有业务日志实时进 WS。
- `WS /v1/logs/ws` 匿名公开（`query.py:496-508` 无鉴权）。
- **已确认会把敏感 URL 打到日志**：
  - `api/storage/redis_adapter.py:151`：`log.info("Redis 存储驱动已成功连接: %s", self._redis_url)` —— 含 `redis://:密码@host` 完整连接串；`_redact` 的模式（`log_buffer.py:20` 需 `password=` 形式）也盖不住该写法。
  - `api/alerting.py:220`：`log.info("告警 webhook 已外发至 %s（%d 条）", url, ...)` —— 企微/钉钉/Slack webhook URL 尾部即签名/令牌，原样广播。
  - `api/imagefree_client.py:437`：publicUrl（可能为预签名 URL）原样进日志。
- 结论：**匿名 WS 客户端可实时收到含 Redis 密码 / webhook secret 的日志流** → 凭据泄露 + 可被 `scripts/ops/` 明文凭证文件之外的又一网络泄露通道。

### 3.2 `/v1/logs` GET 与磁盘日志

- 内存缓冲通道已脱敏（`log_buffer.py:48-57`），但 message 仍可能含**内部路径/异常堆栈**（`log.exception` 输出 file:line，经 `handlers.py:64` 触发的未捕获异常日志），匿名可读（`query.py:490-493`）。P2。
- 磁盘日志（`api/disk_logger.py:13-30`）**无脱敏**，落本机 `IF_LOG_DIR`；文件不可网络直达（P3，本机被攻破后含未脱敏 webhook/Redis URL）。

### 3.3 错误响应不泄内部信息 ✓（达标）

`handlers.py:62-70` 未捕获异常只返回「服务器内部错误」500，不外泄堆栈/路径；429 走 `Retry-After` + 中文 human_hint（`handlers.py:27-47`）；422 委托 FastAPI 默认 detail（仅回显输入值）。**达标**。

### 3.4 访问日志 query 脱敏 ✓（达标）

`context.py:139-148` 仅记 `path` 不含 query；`uvicorn.access`/`httpx` `propagate=False`（`main.py:39,43`）；`?api_key=` 弱通道打 warning 只记 path（`auth.py:102-108`）。**达标**（前提：`logs/ws` 通道修复后）。

### 3.5 匿名公开端点泄露内部/运营数据（P2 组）

| 端点 | 位置 | 泄露内容 |
|---|---|---|
| `/v1/diagnostics` | `query.py:666-720` | `config.DB_FILE` **绝对路径**、磁盘容量/可写性、worker/token 池/solver 内部构成 |
| `/v1/proxy-pool`、`/v1/proxy-pool/subscribe` | `query.py:511-516,558-570` | 基础设施 IP:端口清单、一键订阅导出（见 4.2） |
| `/v1/routing/records` | `query.py:573-590` | MAB-EWMA 节点评分/熔断状态（可据此规避风控、定位健康节点） |
| `/v1/slow`、`/v1/slow/view` | `query.py:593-663` | 任务级 queue/upstream 耗时、trace_id |
| `/v1/providers` | `query.py:79-104` | **credits 实时余额**（号池经济情报） |
| `/v1/cost` | `query.py:107-189` | 月成本/预算/燃烧率/by_provider/by_model（运营财务数据） |
| `/v1/account-pool` | `query.py:214-272` | `register_ip`（账号注册 IP 全量）、checkin 时序——虽 email 已脱敏，注册 IP 仍可关联 |
| `/v1/errors` | `query.py:417-435` | 失败任务的 `prompt_preview`（用户输入片段） |
| `/v1/tasks`/`/v1/tasks/{id}` | `tasks.py:20-49` | 公网 IP（私网已脱敏 `queries.py:850-855` ✓），但任务 prompt 对任何匿名者可读 |
| `/v1/chat/usage`、`/v1/chat/remaining` | `chat.py:770-772,834-836` | 额度/成本统计公开 |
| `/v1/agent/memory` | `agent/routes.py:115-145` | `user_key` 共享（默认 `"default"`）：任意人可读同一记忆桶（无租户隔离，见 4.3） |

其中 `diagnostics` 的 DB 路径与 `account-pool` 的 register_ip 属于**内网拓扑/隐私关联信息**，建议收紧。

---

## 4. 凭证残留

### 4.1 源码层（api/ 全量，排除 tests）

`rg` 扫描 `api/`（排除 `tests/`、`*.example`）：**未发现生产级明文密钥**（sk-/tfai-/ghp_/aws 长串等均无）✓。`scripts/ops/`（gitignore）存在生产明文凭证文件——**按要求仅标注「存在待轮换」，未读取内容**，建议任务清单轮换。

### 4.2 免费代理订阅硬编码共享凭据（P3）

`api/geo_ip.py:249` 硬编码 vmess UUID（`b831381d-...`）、`:265` 硬编码 ss 密码 `"freeProxy"`——全部代理节点共享同一密钥且订阅端点**匿名可拉**（`query.py:558-570`）。虽为免费代理占位凭据（非真实付费凭证），但对外暴露了**基础设施节点 IP:端口清单 + 统一弱密钥**，可被利用做目标侦察或免费代理滥用。另 `geo_ip.py:116` 在线 IP 归属查询走第三方 `ip-api.com`（`query.py` 的 task_to_public 每任务可能触发），属隐私外呼（P3）。

### 4.3 agent 技能/记忆库默认口令（P2）

`/v1/agent/memory` 查询与 `memory/observe`（`agent/routes.py:115-157`）无用户隔离：`user_key` 默认 `"default"`，任意匿名者（仅 per-IP 限流）可**读写同一记忆桶**——污染他人记忆（`observe`）或窃取记忆内容（`query`）。同一模式在 DAG `_exec_memory`（`agent_dag_exec.py:214-235`）。属设计口径「非多租户」，但公开写接口下是**未鉴权写 + 跨用户数据污染**面。

---

## 5. CORS / CSP

### 5.1 CORS 全开放现状（P1 已知 + P2 加固待授权）

- 代码默认 `IF_CORS_ORIGINS=*`（`api/config/__init__.py:210,817`、`api/config/security.py:20`），`main.py:126-131` `CORSMiddleware(allow_origins=[*], allow_methods=["*"], allow_headers=["*"])`，**未设 allow_credentials**（starlette 对 `*`+credentials 会拒绝，此处无 cookies，风险收敛为「任意站点脚本可直接调用公开端点」）。
- 生产模板已收紧 `IF_CORS_ORIGINS=https://imagefree.tingfengai.art`（`deploy/.env.production.example:21`），**但 memory 记录线上仍为 `*`（workflow_status P0-1 已知待授权项）**——未落地，需授权收紧（结合 2.4/3.5，所有匿名公开端点对任意来源可读）。
- 附：CORS `*` 不配合 `allow_credentials` 对管理端点（Bearer 头）无直接利用，但公开数据面（运营/成本/号池）全跨域可达。

### 5.2 CSP 缺失（P2）

`IF_CSP_ENABLED` 默认 **False**（`config/security.py:26`），生产模板同样 `IF_CSP_ENABLED=0`（`deploy/.env.production.example:47`）→ **生产无 Content-Security-Policy 头**。`SecurityHeadersMiddleware`（`main.py:49-106`）虽默认注入 X-Content-Type-Options / X-Frame-Options / Referrer-Policy / HSTS（仅 HTTPS），但敞 CSP 意味着 XSS 无纵深缓解；且 CSP 示例本身含 `script-src 'unsafe-inline'` 与宽松 `style-src`（`main.py:69-75`）——即便开启也偏宽松（管理面板内联脚本的代价）。审慎对待 gallery/管理面板展示用户 prompt 与外部图床 URL 的 XSS 面。

---

## 6. 注入面

- **SQL**：`/v1/admin/export/tasks` 的 where 子句 `f-string` 拼接（`query.py:819-827`）+ **全值参数化 `?`**，无注入；画廊 `search` 子串搜索走 LIKE 参数且在查询层传参（`gallery.py:364,396`）→ **无注入** ✓。
- **命令执行**：`rg` 扫 `api/` 无 `subprocess shell=True`/`os.system`；`is_destructive_command` 硬门禁拦截破坏性指令（`agent/guard.py`，调用点 `agent_dag_exec.py:250`、`chat.py:98`）✓。
- **SSRF**：出站 httpx 目标全部来自配置/环境（TensorFeed `ecosystem.py:77`、webhook `alerting.py:216`、redis `redis_adapter.py`、代理池），**无用户可控 URL 出站**；`solver_guard.py:85` 有 SSRF 守卫（拦链路本地/云元数据 IP）✓；`video.py` `image_url` 字段已定义但**未使用**（未传 provider）✓。
- **文件路径**：`task_to_public` 读 `file://` 前缀路径（`queries.py:838-848`）——值来自 DB 内部写入（非用户直控），低危；`slow/view` 为静态文件。**无路径穿越写** ✓。
- **CSV 注入**：导出已转义 `=+-@` 前缀（`query.py:758-764`）✓。

---

## 7. 分级汇总

| 级别 | 编号 | 问题 | 位置 |
|---|---|---|---|
| P1 | S-1 | `/v1/logs/ws` 匿名 WS 广播未脱敏 → webhook URL 与 Redis 连接串（含密码）实时泄露 | `log_ws.py:56-68`、`lifespan.py:41,48`、`alerting.py:220`、`redis_adapter.py:151`、`query.py:496-508` |
| P1 | S-2 | `IF_TRUSTED_PROXIES` 未含反代 IP → 生图限流/封禁按反代单桶 + 自动封禁可误伤全站（3 次超限拉黑反代=全站 403） | `request_guard.py:172-187,488-524,555-559`、`config/security.py:15` |
| P1 | S-3 | 聊天/DAG/视频/PPT/MCP 限流未走受信代理解析（用 socket 对端）→ 反代后全站单桶共享、可耗尽公益额度 | `auth.py:186-187,223-224`、`chat.py:565,611`、`video.py:43,84`、`ppt.py:40`、`mcp/server.py:201` |
| P1 | S-4 | `POST /v1/tasks/{task_id}/cancel` 零鉴权零限流（可取消任意他人任务） | `tasks.py:52-69` |
| P1 | S-5 | DAG/agent 全链无 Key 真实执行（IF_MOCK_UPSTREAM=0、预算门禁默认 off、免费上游估 0）→ 换 IP 无限刷免费额度直至耗尽 | `agent_dag.py:133-193`、`agent_dag_exec.py:129-175,310-347`、`budget_guard.py:29-35`、`config:299,319` |
| P2 | S-6 | 生产无 CSP 头（默认 0 且生产模板 0），XSS 无纵深缓解 | `config/security.py:26`、`main.py:49-106`、`deploy/.env.production.example:47` |
| P2 | S-7 | 线上 CORS 仍 `*`（已知 P0-1 待授权），公开运营端点任意跨域可读 | `main.py:126-131`、`config:817` |
| P2 | S-8 | 匿名公开端点泄露内部/运营/成本/号池情报（诊断路径/代理节点/路由评分/余额/注册 IP/prompt 片段） | `query.py`（见 3.5 表） |
| P2 | S-9 | `/v1/chat/usage`、`/v1/chat/remaining` 匿名公开（成本/额度外泄） | `chat.py:770-772,834-836` |
| P2 | S-10 | `POST /v1/errors/frontend` 匿名可写（污染错误聚合） | `query.py:451-470` |
| P2 | S-11 | `GET /v1/dead-letter-queue` 无 Key 公开 | `write.py:19-23` |
| P2 | S-12 | agent 记忆库无租户隔离（user_key=default 共享，匿名可读写同一记忆桶） | `agent/routes.py:115-157`、`agent_dag_exec.py:214-235` |
| P2 | S-13 | 管理 Key 未配时继承业务 Key（单 Key 职责混合，泄露即得管理权） | `auth.py:47-56` |
| P2 | S-14 | 画廊密码/签名默认空 → 画廊与打包公开；`/v1/tasks/{id}/retry` 无 Key 可复制他人任务重跑 | `query.py:323-333,357-403`、`tasks.py:72-103` |
| P2 | S-15 | `_client_ip_of` 异常 fallback 信任 XFF 首段（可伪造取证 IP） | `auth.py:262-274` |
| P3 | S-16 | 免费代理订阅硬编码共享 UUID/密码 + 匿名可拉基础设施节点清单；`task_to_public` 触发第三方 IP 归属在线查询 | `geo_ip.py:249,265-267,116`、`query.py:558-570` |

---

## 8. 建议处置优先级

1. **立即（P1）**：`log_ws.py` 复用 `log_buffer._redact`；`redis_adapter.py:151`、`alerting.py:220` 日志脱敏后再记（或只记 host）；上线前核对 `IF_TRUSTED_PROXIES` 含真实反代 IP；`tasks.py cancel` 补 `_prepare`-级 guard；DAG/agent 端点补弱 Key 或把限流下限收紧到不可换 IP 绕过的程度。
2. **短期（P2）**：开启 `IF_CSP_ENABLED`（收敛 script-src 去掉 unsafe-inline 或加 nonce）；授权收紧线上 CORS；`query.py:490-508` 日志端点与管理端点加 `check_admin_key`（或在保留公益前提下脱敏+限流）；`chat/usage`、`dead-letter-queue` 加 Key；`errors/frontend` 加防刷（IP 限流/签名）；记忆库引入 user_key 绑定鉴权。
3. **长期（P3）**：代理订阅改一次性签发（含过期）；`user_key` 多租户化；`register_ip` 对外脱敏。

---

*审计者为只读顾问角色，未修改任何文件；证据均为源代码静态确认，未经运行验证（`verify` 标签：静态确认）。*