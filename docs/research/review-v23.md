# v23 改动六维独立审查报告（N7 · Critic 独立复验）

- 审查对象：v23 批次全部改动（工作树 vs HEAD，含新增资产 `deploy/systemd/`、`scripts/probe_federation.py`、`docs/research/*-v23.md`）
- 审查方式：对抗性独立复验，**只读未改任何文件**；结论全部基于实际代码/实际运行
- 日期：2026-09-28
- Verdict：**NEEDS_CHANGES**（2 个 P1 需修复后再合入；无 P0 数据破坏级问题）

---

## 0. 验证记录（实际运行证据）

| 命令 | 结果 | 说明 |
|---|---|---|
| `py -m pytest tests/test_db_batch_write.py tests/test_db_indexes.py tests/test_log_ws.py tests/test_task_cancel.py -q` | 45 passed, exit 0 | v23 四组后端新用例全绿 |
| `py -m pytest tests/test_gallery_crud.py tests/test_db_retention.py -q` | 21 passed, exit 0 | 画廊白名单改动的回归面无破坏 |
| `npx vitest run src/test/Tasks.test.tsx src/test/api.test.ts` | 2 files / 83 tests passed | F2 换算 + Tasks navigate 用例真实通过 |
| `npx tsc -b --pretty false` | exit 0 | 前端类型检查通过 |
| `py scripts/probe_federation.py --n 5 --total 8 --out /tmp/probe_test.json` | exit 0，双节点承接分布 2/3、3/2、4/4 | 探针真实可运行 |
| 探针后 `netstat` 检查 8001/8002 | 仅 TIME_WAIT，无 LISTENING | mock 进程清理有效（finally terminate 闭环验证） |
| `py -c` 直调 `_redact` 边界样本 | 见 §4 P2-2 | 实证含 `@` 密码部分泄露、短 key 不脱敏 |

---

## 1. 六维逐项结论

### 1.1 需求完整性
- S-1（日志脱敏）三层落地（`log_ws` 广播复用 `_redact`、`redis_adapter` 源头脱敏、`alerting` webhook 脱敏）✅
- S-3（受信代理解析）`check_chat_rate_limit`/`check_dag_rate_limit` 收敛到 `_client_ip_of`，且 video/ppt/mcp 全部经 `guard_chat_request` 自动继承修复 ✅（实证）
- S-4（cancel 补 guard）只补了限流，未落审计（见 P2-4），且文档声称的"IP 回填"未实现 ❌
- F1/F2/N4-P1/N4-P2 前端项均与后端契约实测一致 ✅（`gallery.py:334` 确需 admin key；`handlers.py:42` 确在 `error` 顶层放 `retry_after_seconds`；Costs 错误消息因 caller 含 "HTTP 401" 判断可靠）
- N2 索引补齐幂等增量、列先于索引 ✅
- **需求缺口（P1）**：`_flush_buffer`"失败回写重试"缺重试上限/隔离机制，未完成"失败不丢"的完整闭环（见 P1-1）；`deploy/systemd` 交付物不可用（见 P1-2）

### 1.2 逻辑正确性
- `_flush_buffer` 单次失败→回写→成功 的路径正确（测试实证）；但**永久失败路径死锁**（P1-1）
- gallery 白名单与旧 NOT IN 在当前引擎数据流下语义等价（image_url 仅 `mark_finished(completed/error)` 落库，cancelled/processing 无图——engine.py:657-674 实证）；但把状态词汇表硬编码进查询属脆弱耦合（P2-3）
- `_safe_redis_host` 对无密码 `redis://host`、unix socket、异常 URL 均不泄露（兜底 `<redis>`/默认 host）✅
- cancel guard 与 `IF_TASK_CANCEL_ENABLED` 先后顺序正确 ✅

### 1.3 边界情况
- **redis 密码含 `@` → 部分泄露**（实证 `redis://user:pa@ss@host` → `***@ss@host`，P2-2）
- `_redact` 短值 key（<6 字符）不脱敏（`{6,}` 下限），中文上下文 `key=foo` 不匹配（P3-3）
- `_flush_buffer` 头部阻塞 + 无界增长（P1-1，最重边界）
- cancel 与 chat 共享 60/min 桶：重聊天用户在相同分钟内取消可能 429（P3-4）
- IF_TRUSTED_PROXIES 默认仅 `127.0.0.1,::1`：生产同机 nginx 可用；若换云 LB 私网反代需显式配置，否则 S-3 失效（P3-6）

### 1.4 代码质量
- 改动手法整体收敛：源头脱敏、广播复用单一 `_redact`、guard 收敛单一判定函数，符合"统一入口"原则 ✅
- `_flush_buffer` 失败分支逻辑集中在 except 内，可读性尚可；缺重试计数器/隔离态（P1-1）
- `observability.service` 单文件三组 unit 段 + README 引用不存在文件（P1-2，资产自洽性失败）
- `cancel_task` docstring 过度承诺（P2-4）

### 1.5 测试覆盖
- 新增用例：`_flush_buffer` 部分失败回写 ✅、v23 索引存在性 ✅、log_ws 脱敏（redis/api_key/webhook）✅、cancel guard 全路径 ✅、F2 换算 ✅、Tasks navigate ✅
- **缺口（P2-5）**：永久失败/无限重试路径无用例；Gallery 删除部分失败与 401 无用例；Costs forecast 降级分支无用例；Security confirm 无用例；gallery 白名单语义（cancelled/processing 无图）无显式断言

### 1.6 实际运行证据
- 见 §0 验证记录。测试真实性确认，非纸面通过。
- 探针进程清理闭环验证（8001/8002 无残留 LISTENING）✅

---

## 2. 发现清单（按严重度排序）

### P1-1 — `_flush_buffer` 永久失败 → 无限重试 + 写缓冲无界增长 + 头部阻塞（数据卡死）
- 位置：`api/db/core.py:313-321`（`_flush_buffer` except 分支）
- 证据：
  ```python
  except Exception as e:
      remaining = buf[idx:]   # 含失败条本身，无条件回写
      self._write_buffer = remaining + self._write_buffer
      log.warning(...)
  ```
  `start_batch_timer` 每 `batch_window`（0.2s）flush 一次，无重试计数、无丢弃、无隔离。
- 复现/影响：应用侧任一语句永久失败（如 `ALTER` 未跟上导致 `no such column`、NOT NULL/FK 约束冲突、坏 SQL）→ 每次 flush 都在第 0 条失败 → `remaining = 整缓冲` → 其后的**全部新写入永久无法落库**（头部阻塞），`_write_buffer` 随新写入**无界增长（内存泄漏）**，且每 0.2s 一条 `log.warning`（日志风暴）。服务重启后缓冲全丢 → 静默数据丢失。普通瞬时错误（锁竞争）可自愈，但确定性失败即永久卡死。
- 修复建议：每条语句带重试计数（如连续 N 次失败移入 dead-letter 队列并告警，或丢弃 + 计错误指标），避免头部阻塞；同一条失败日志去重/降频。

### P1-2 — `deploy/systemd` 观察栈资产不可用（多 unit 塞进单文件 + README 引用不存在文件）
- 位置：`deploy/systemd/observability.service`（3 组 `[Unit]/[Service]/[Install]`，行 12/17/32 / 36/40/53 / 57/62/82）、`deploy/systemd/install_observability.sh:29`（`sed` 后写单一 `imagefree-obs.service`）、`deploy/systemd/README.md:14-16`（列出的 `prometheus.service`/`node_exporter.service`/`grafana.service` 均不存在）
- 证据：
  - 单文件含 3 组同名单节 → systemd 无法据此生成 3 个 unit；生成的 `imagefree-obs.service` 只会保留最后一个 `ExecStart`（grafana），**prometheus 与 node_exporter 永不启动**。
  - README 手动路径 `systemctl enable --now imagefree-prometheus imagefree-node-exporter imagefree-grafana` 引用不存在的 unit 文件。
- 影响：N6 交付物"观察栈一键装载"按其说明无法达成；生产 apply 后观察栈不成立（探测不到 API 指标）。
- 修复建议：拆成 3 个独立 `.service` 文件（`prometheus.service`/`node_exporter.service`/`grafana.service`），install 脚本逐个装载；或改用 `systemd-run`/模板化；README 资产表与脚本保持一致。

### P2-1 — 观察栈权限/密码配置问题（继承 P1-2 修复范围）
- 位置：`install_observability.sh:28`（root `mkdir -p /var/lib/imagefree-prometheus|imagefree-grafana|/var/log/imagefree-grafana`）+ `observability.service` `User=ubuntu`
- 影响：prometheus/grafana 以 ubuntu 运行但数据/日志目录属 root → 启动即权限失败（无 `chown`/`StateDirectory=`）。
- 另：`observability.service:76` `Environment=GF_SECURITY_ADMIN_PASSWORD=${GF_ADMIN_PASSWORD:-admin}` 是 shell 语法，systemd 不做 `:-default` 展开 → 密码为字面量/空；且 `GF_AUTH_ANONYMOUS_ENABLED=true` 匿名开放面板（仅 127.0.0.1 绑定缓解）。
- 修复建议：脚本对目录 `chown -R ubuntu:ubuntu` 或 unit 用 `StateDirectory=`；密码改 unit 内显式 + 部署时写 `.env` 注入；匿名访问按需关闭或经 nginx basic auth。

### P2-2 — redis 脱敏正则对含 `@` 的密码部分泄露
- 位置：`api/log_buffer.py:27`
- 实证：`_redact("redis://user:pa@ss@10.0.0.5:6379/0")` → `redis://***@ss@10.0.0.5:6379/0`，密码片段 `ss` 残留外泄。
- 修复建议：用贪婪向后匹配（`(?i)\bredis(?:sentinel)?://(?:[^@\s]*:)?[^@\s]*@` 或直接剥 `://` 到最后一个 `@`），并对 `@` 后的 host 部分再确认不含 userinfo。

### P2-3 — gallery 正向白名单的状态词汇硬编码（现状无回归，未来脆弱）
- 位置：`api/db/queries.py:332-338`
- 证据：image_url 仅由 `mark_finished` 写入，engine 仅传 `completed`（有图）/`error`（无图），`cancelled`/`processing` 行不可能有图（engine.py:657-674、queries.py:147-151、388-400 实证）；因此现状下白名单与旧 NOT IN 语义等价，**无功能回归**（test_gallery_crud/retention 21 用例全绿佐证）。
- 但：白名单把"哪些状态可带图"硬编码进查询，未来新增带图终态（如 partial/legacy 导入）会静默消失；且 `failed` 是死状态（engine 从不写）。建议以单一状态常量表为源、查询引用之，并加一条"cancelled/processing 无图行不出现"的显式测试。

### P2-4 — cancel_task 文档声称"调用方 IP 回填审计"但代码未实现
- 位置：`api/routes/tasks.py:59-61`（docstring）vs 代码仅 `guard_chat_request(request)`（:70）
- 证据：`guard_chat_request` 不写 `request.state.client_ip`；全库消费 `state.client_ip` 的只有 `guard_generate_request`（auth.py:262）与 `routes/generate.py:56`。cancel/retry 路径无任何 IP 落库。S-4 审计项实际未落地。
- 修复建议：要么在 cancel 路由 `request.state.client_ip = _client_ip_of(request)` 并随审计落库，要么改 docstring 去掉"IP 回填审计"表述。

### P2-5 — 关键行为缺测试（覆盖缺口）
- `_flush_buffer` 永久失败/无限重试/头部阻塞：无用例（现有用例只覆盖"失败一次后手动清障再成功"）
- Gallery 删除：部分失败保留项、401/403 带 adminHeaders 行为无用例
- Costs forecast：`401/403` 降级块渲染与重试无用例
- Security 封禁二次确认：无用例
- gallery 白名单：无"cancelled/processing 无图行不出现"断言

### P3 级（记录，不阻塞）
- P3-1：`workflow_status.md` N5"均时 2.9s vs 单 6.7s"与 `solver-federation-v23.json` 实际数字（select_node 2.12s / 单节点 0.18-0.27s / 持续压测 0.90s）不一致，疑似不同参数或表述夸大，需对齐。
- P3-2：索引"覆盖"声明未用 `EXPLAIN QUERY PLAN` 实证；`idx_requests_status_dur(status,duration_sec,created_at)` 对 `stats_overview` 的 `status != 'archived'` 谓词是乐观覆盖，SQLite 规划器行为需实测确认。
- P3-3：`_redact` 短敏感值（`key=abc` 等 <6 字符）不脱敏（`{6,}` 下限）；中文紧邻文本 `key=foo` 不匹配。属防误伤取舍，建议明确阈值口径。
- P3-4：cancel 与 chat 共享 `_chat_buckets` 60/min 桶，重聊天用户在窗口内取消会被 429；无独立 cancel 配额。
- P3-5：SOP "路径改 /opt/data" 笔误（应为 `/opt/imagefree-api/data/`）；`litestream-restore.md` 用 `-config deploy/litestream.yml.prod` 而启用流程复制为 `deploy/litestream.yml`——两个路径在服务器上均存在，可工作但口径应统一。
- P3-6：`IF_TRUSTED_PROXIES` 默认仅 `127.0.0.1,::1`；生产同机 nginx 可用，若拓扑改为云 LB 私网反代需显式配置，否则 S-3 限流退化回单桶。

---

## 3. 已确认无问题（对抗性核查通过项）

- **S-1 三层脱敏**：`log_ws.broadcast_log` 复用 `_redact`；`redis_adapter.py:173` 与 `alerting.py:239` 源头脱敏；`_safe_redis_host`/`_safe_webhook_url` 对异常输入兜底不泄露。
- **S-3 传播完整性**：video/ppt/mcp 全部经 `guard_chat_request` → `check_chat_rate_limit` → `_client_ip_of`（受信代理判定），修复自动全覆盖。
- **F1 画廊删 Key**：后端 `gallery.py:334` 确需 `check_admin_key(scope="gallery-delete")`，前端合并 `adminHeaders()` 是**必要且正确**的修复；删除失败保留项逻辑正确（`failed` 集合与 `itemKey` 一致，实证 selected 存的是 item key）。
- **F2 429 字段**：`handlers.py:42` 在 `error` 顶层放 `retry_after_seconds`，`getErrorPayload` 读 `nested`（=body.error）路径正确，90→2 换算用例通过。
- **N4-P1 Costs 降级**：`fetchCostForecast` 带 `caller:'成本预测获取失败'` → `message="成本预测获取失败 HTTP 401: …"` 含 "401"，`includes('401')` 判断可靠。
- **N4-P2 Tasks navigate**：`App.tsx:33` `BrowserRouter basename={BASE_URL}`（生产 /admin）→ `navigate('/generate')` 解析正确，不再 404。
- **N4-P2 Security confirm**：封禁补确认，与解封口径一致。
- **探针**：SolverGuard 所需 API（select_node/select_candidates/record_*/acquire_inflight_for/snapshot）全部存在；mock_cfsolver 支持 `--node-id/--delay`；进程清理 finally 闭环实测无残留。

---

## 4. 结论与建议

- **总体 Verdict：NEEDS_CHANGES** —— 主链路（DB/限流/脱敏/前端）逻辑正确、测试真实通过、无 P0；但 v23 两个新交付物存在 P1：
  1. `_flush_buffer` 失败重试缺上限/隔离 → 永久失败时写链路卡死 + 内存无界增长（建议在合入前加"N 次失败隔离/丢弃 + 告警"）；
  2. `deploy/systemd` 观察栈单文件多 unit 结构 + README 引用不存在文件 + 目录权限 → 交付物按其说明不可用（建议拆 3 个独立 unit + chown/StateDirectory + 对齐 README）。
- 其余 P2（redis 含 @ 密码部分泄露、cancel 审计未落地、测试缺口）建议同批修复；P3 记录不阻塞。
- 修复后建议：重跑 §0 全部验证 + 补 P2-5 列出的缺口用例后再合入。

*本报告仅反映代码/运行事实，不臆造结论；systemd 语义基于文档静态分析，最终以生产 `systemd-analyze verify` 复核为准。*

---

## 附：修复复验结论（第二轮，2026-09-28）

针对首轮 NEEDS_CHANGES 的全部修复项逐点复验如下。结论：**APPROVE（有条件）**——首轮全部 P1/P2 已修复并经实际运行确证；余下 P3 与 2 个非 v23 引入的既有注意项。

### 逐项复验（聚焦修复点，未重复未受影响的检查）

| 首轮项 | 修复内容 | 复验证据 | 结论 |
|---|---|---|---|
| P1-1 `_flush_buffer` 无限重试/头部阻塞 | `BatchWrite.attempts` + `_MAX_WRITE_ATTEMPTS=5`，超限丢弃（`_dead_letters`+error 日志），剩余条继续回写（`api/db/core.py:69-85,117,301-338`） | 逻辑核查：attempt 计数挂在失败头条，`remaining[0].attempts` 边界安全（buf 进入时已保证非空）；隔离后 `remaining[1:]` 继续回写。新测试 `test_flush_permanent_failure_discarded_after_attempts`（monkeypatch 上限=3，验证 2 次回写 → 第 3 次丢弃 → 后续正常条落库）真实通过。`test_db_batch_write.py` 16 用例全绿 | ✅ 已修 |
| P1-2 systemd 单文件多 unit | 拆 3 个独立 unit（`prometheus.service`/`node_exporter.service`/`grafana.service`），各含单组 `[Unit]/[Service]/[Install]`；`install_observability.sh` 逐个装载 3 服务 + `grafana-server` 二进制名 + 目录 `chown -R ubuntu:ubuntu`（P2-1） | 三文件均为合法单 unit（逐段核查）；脚本移除原 `sed` 合并、改 `cp` 三 unit + `systemctl enable --now` 三服务 + 步骤 6 验证；README 资产表与文件实存一致 | ✅ 已修 |
| P2-2 redis 含 `@` 密码部分泄露 | `log_buffer.py` redis 模式改贪婪 `[^\s]+@`（脱到最后一个 @） | **实测**：`_redact("redis://user:pa@ss@10.0.0.5:6379/0")` → `redis://***@10.0.0.5:6379/0`（完整掩码）；unix socket 与无密码 URL 不受影响。`test_log_ws.py` 8 用例全绿 | ✅ 已修 |
| P2-3 gallery 白名单硬编码 | `GALLERY_VISIBLE_STATUSES` 单一常量源 + 参数化占位符（`queries.py:23-29,338-342`）+ 2 新测试 | 新测试：completed 有图默认可见；pending/processing/queued 有图被排除——真实通过（`test_gallery_crud.py` 10 用例全绿） | ✅ 已修 |
| P2-4 cancel 审计过度承诺 | docstring 删除"IP 回填审计"表述，诚实披露仅 per-IP 限流、审计需另加字段（`tasks.py:59-64`） | 代码与 docstring 现已一致；`test_task_cancel.py` 13 用例全绿 | ✅ 已修 |
| P3-1 workflow_status 数字 | N5 改为与 `solver-federation-v23.json` 一致（串行 0.18s/0.27s、联邦并发 2.1s 含本机排队污染、承接分布为硬结论） | 与 artifact JSON 逐项比对相符 | ✅ 已修 |
| P3-5 `/opt/data` 笔误 | SOP/restore 文档全改为 `/opt/imagefree-api/data/` | `grep "opt/data" docs/SOP.md deploy/docs/litestream-restore.md` 零命中 | ✅ 已修 |

### 复验命令与真实输出摘要

- `py -m pytest tests/test_db_batch_write.py tests/test_db_indexes.py tests/test_log_ws.py tests/test_task_cancel.py tests/test_gallery_crud.py -q` → **56 passed, exit 0**（对应 coordinator 声称的 db_batch 16 / gallery 10 / log_ws 8 等）
- 本地 vitest 4.1.11 + jsdom（权威工具链）：`Tasks.test.tsx` 隔离 **4/4**；`Tasks.test.tsx + api.test.ts` **83/83**；全量 **296 passed / 3 failed（28 files / 299 tests）**
- `npx tsc -b --pretty false` → **exit 0**；`npm run build` → **✓ built in 48.27s, exit 0**

### 复验中的独立发现（非首轮项，待处置）

- **P2（HEAD 既有，非 v23 引入）**：`frontend/src/test/api.test.ts:128` 与 `:776` 重复 `import { onToast } from '../api'`——在 npx 回退解析的 vitest 下触发 esbuild 硬报错 `Identifier 'onToast' has already been declared`（本机复现）；项目本地 vitest 容忍（83 通过）。属潜在 CI/工具链迁移断裂点，建议合并两处 import（顺手即可修，非 v23 阻断）。
- **P3（环境性 flaky，与 v23 无关）**：全量 vitest 3 个失败均为**时序超时类**且失败集在两次运行间漂移（CostsPage 导出 / GalleryAlbum 多选 / Tasks SSE 徽章），隔离复跑 Tasks 4/4 通过；CostsPage 导出用例 5s `testTimeout` 在本机慢 jsdom 环境偏紧。不构成 v23 回归。
- **P3（待生产验证）**：`grafana.service` 的 `--homepath=/opt/imagefree-api/deploy/grafana` 需该目录含 grafana `public/` 前端资产，install 脚本目前仅拷贝 `bin/grafana-server` 二进制——生产 apply 时若起服失败，需补拷贝 `public/`（模板状态，脚本步骤 6 的 `systemctl is-active` 可现场发现）。
- **P3（P1-1 设计权衡）**：连续 5 次 flush 失败的语句被当作永久失败丢弃（约 1s 窗口）——长瞬态（>1s）会丢单条语句。属有界化取舍，docstring 已披露；`_dead_letters` 计数器当前未暴露到观测端点。

### 更新后 Verdict

**APPROVE（有条件）**：首轮全部 P1/P2 修复已逐点确证（代码 + 实际运行），后端 56 用例 + 前端 296/299（3 项时序 flaky 与 v23 无关）+ tsc 0 + build 成功。建议：合入前顺手合并 `api.test.ts` 重复 `onToast` import（P2 既有项）；Production apply 前按 P3-待验证项核对 grafana `public/` 资产。
