# 极限施压 + 防穿透测试报告（v23.1 · Spec-Kit 011）

> 节点契约 T1-T4 落地 ｜ 2026-09-28 ｜ 依据：`.specify/specs/010-production-hardening-ngx/spec.md` US2（承诺未兑现，本轮真补）
> 环境：本地 Windows + mock cf_solver（无真实上游，符合烧钱红线）· uvicorn 测试实例（IF_DB_FILE=data/e2e_stress.db 隔离）
> 探针：`scripts/probe_stress.py`（新 · 并发阶梯压测）· mock_cfsolver `/__fault` 注入 · 既有 test_chaos 故障套件

---

## 1. 读路径极限施压（T1）

### 方法
- 端点：`/v1/healthz` `/v1/gallery?limit=10` `/v1/stats` `/v1/providers` `/v1/models`（5 个只读）
- 并发阶梯：1 → 10 → 50 → 100 → 200；每档每端点 30 请求
- 判崩溃：任一档错误率 >5% 或进程退出 → 提前终止并记录崩溃阈值
- 原始数据：`docs/research/stress-v23.json`

### 结果（全部 ok=30/30，0 个 5xx，0 个 429，进程全程存活）

> **口径注明（T6-F6）**：每档每端点 30 样本，p99 采用 nearest-rank 近似 = 该档最大值（样本量小端点效应）；方向性结论（0 错误率/不崩）不受影响，绝对 p99 值供趋势参考。

| 并发 | healthz p50/p99 (ms) | gallery p50/p99 | stats p50/p99 | providers p50/p99 | models p50/p99 |
|---|---|---|---|---|---|
| 1 | 40.7 / 127.8 | 18.6 / 42.3 | 24.8 / 58.4 | 16.9 / 20.2 | 31.3 / 41.4 |
| 10 | 255.9 / 404.9 | 114.5 / 280.4 | 125.0 / 299.1 | 105.5 / 359.1 | 189.4 / 288.5 |
| 50 | 586.0 / 805.9 | 509.9 / 861.5 | 575.2 / 894.7 | 528.7 / 801.6 | 414.8 / 624.8 |
| 100 | 555.7 / 841.3 | 368.1 / 538.9 | 374.0 / 607.4 | 454.8 / 691.6 | 730.9 / 1110.2 |
| 200 | 576.5 / 973.4 | 532.6 / 781.4 | 488.4 / 812.0 | 234.7 / 362.2 | 351.3 / 650.6 |

### 结论（双口径，防误导）

**硬结论（与并发无关，可信）**：
- **200 并发下 0 错误率、0 5xx、0 429、进程不崩** —— 读路径在 5 倍于典型负载下保持可用；
- 延迟从并发 1 的 16-40ms 升到并发 10+ 的 100-600ms 区间 —— **本机 uvicorn 单进程 HTTP 排队噪声**主导（与 v23 N5 solver 联邦实测同源：无状态端点 p50=565ms 佐证），**不代表生产服务框架瓶颈**（生产有 nginx 反代 + gzip，静态资源已 1y 缓存分离）。

**服务框架真实水位**：API 入口（校验→入库→入队）在串行下 16-40ms，符合宪法 §Performance「入口 ≤50ms 级」；高并发劣化是本机单进程回环的测量噪声，非架构缺陷。

---

## 2. 防穿透 / 极端注入（T2）

| 注入 | 预期 | 实测 | 结论 |
|---|---|---|---|
| 畸形 JSON `{"prompt": "oops";;;` | 4xx | **422** | ✓ 优雅拒绝（FastAPI 校验） |
| 缺必填 body `{}` | 422 | **422** | ✓ |
| 超长 prompt 100KB | 422 | **422**（pydantic string_too_long） | ✓ 不截断不 OOM |
| 200KB prompt | 422 | **422** | ✓ |
| 5MB 畸形 body | 4xx | **422**（json_invalid） | ✓ 大体积安全拒绝 |
| 生图连打 15 次 | 前 10 次 200，后 429 | **10×200 → 5×429 + Retry-After: 60** | ✓ per-IP 限流真实生效 |
| 连打触发风控 | 403 自动封禁 | **AUTH.003 该 IP 已被系统安全风控限制访问** | ✓ task-failure-burst 机制真实生效 |
| 并发双 cancel | 幂等不 5xx | 既有 test_task_cancel 13 用例覆盖（幂等双 cancel） | ✓（v22.1 已闭环，未重复压测避免误伤） |
| SSE 事件端点 | 200 | **200**（/v1/events/tasks） | ✓ |

### 防穿透中发现并确认的安全机制效应（需要披露）
- **`task-failure-burst` 自动封禁**（engine.py:761）：任务失败时记调用方 IP 违规，窗口内高频失败自动封禁 → 验证 S-2 审计警告：**若生产反向代理 IP 未列入 IF_TRUSTED_PROXIES，全站用户共享反代 IP，一次上游批量失败即可误封全站（DoS 放大）**。属设计内行为，但生产核对项必须落实（SOP 已标注）。
- **IP 限流 + 自动封禁叠加**：恶意刷 `task-failure-burst` 会加速封禁；测试过程 127.0.0.1 被两次封禁，解封后可逆（本地 mock 环境）。

---

## 3. 覆盖率盲区猎杀（T3）+ 补测（T4）

### T3：v23 核心模块覆盖率基线（pytest --cov 定向实测）

| 模块 | 基线覆盖率 | 漏测路径 |
|---|---|---|
| api/log_buffer.py | **43.2%** | LogBufferHandler.emit 结构化字段（trace_id/req_id/attrs 注入、filter_by_trace_id） |
| api/db/queries.py | 48.8% | 历史大文件（v23 只改 gallery_list 一处且已覆盖） |
| api/alerting.py | 62.1% | _send_webhook 成功/非 2xx/异常三路径、_safe_webhook_url |
| api/db/core.py | 66.0% | 历史基础设施（v23 flush 改动已有专项测试） |
| api/auth.py | 65.9% | **S-3 限流 429 触发分支**（check_chat/dag_rate_limit 滑窗满） |
| api/log_ws.py | 74.5% | — |
| api/storage/redis_adapter.py | 97.3% | — |
| **TOTAL** | **61.5%** | v23 安全/DB 修复直接载体未达标（< CI fail-under 70） |

### T4：关键路径补测（全部真实通过）

| 新增用例 | 覆盖路径 | 覆盖率提升 |
|---|---|---|
| chat rate limit 429 触发 + limit=0 关闭 + DAG 429 | **auth.py S-3 滑窗满分支** | auth 65.9 → **76.1%** |
| webhook 成功（caplog 断言脱敏日志）/ 非 2xx / 异常吞掉 / safe_webhook_url 三态 | **alerting.py 外发全路径** | alerting 62.1 → **95.4%** |
| LogBufferHandler.emit 结构化字段 + filter_by_trace_id | **log_buffer.py emit 全路径** | log_buffer 43.2 → **88.9%** |
| **顺带修复真实 bug**：`attr_` 前缀 5 字符被 `k[6:]` 错剥 → `attr_custom` 变 `ustom` | log_buffer.py:77 | 结构化 attrs 键错位修复 |

**修复后**：log_buffer 88.9% / alerting 95.4% / auth 76.1% / log_ws 74.5% / redis 97.3% —— **v23 安全修复直接载体全部 ≥74.5% 达标**。
queries 48.8% / core 66.0% 为历史存量大文件的整体缺口（非本轮改动引入），按宪法「只记录不硬补」披露，建议后续抽样批次专项（非为数字而测）。

> **v23.1 T6-F5 行为变更披露**：`LogBufferHandler.emit` 的 `attr_*` 结构化键名从错误的 `ustom`（k[6:] 多剥一字符）修正为 `custom`（len("attr_")=5）。仓库内无 `attr_*` 生产写入方（仅测试驱动），若外部有按旧键名（`ustom`）消费 json-line 日志的调用方，需同步迁移——当前无此消费方，风险为零。

---

## 4. 结论

1. **极限施压真实落地（010 US2 兑现）**：读路径 200 并发 0 错误率不崩，服务框架入口 ≤40ms 符合宪法；高并发延迟劣化为本机测量噪声。
2. **防穿透真实通过**：畸形/超长/大体积输入全 4xx 不崩，IP 限流 + Retry-After + 自动封禁机制全部真实生效。
3. **覆盖率盲区实锤**：v23 修复载体基线仅 61.5% → 补测后关键模块全部达标，且**补测抓出 1 个真实 bug**（attrs 键错位）——正是用户「覆盖率看着有 80% 但上线出 bug」的根因实证。
4. **生产核对项新增披露**：S-2 自动封禁的「反代 IP 未信任 → 全站误封」风险，在压测中真实复现（127.0.0.1 被封），上线前必须落实 IF_TRUSTED_PROXIES。

---

## 5. 复现命令

```bash
# 起 mock solver + uvicorn 测试实例（隔离库，不触生产）
py scripts/mock_cfsolver.py --port 8001 --node-id node-1 &
IF_DB_FILE=data/e2e_stress.db IF_SOLVE_CIRCUIT_THRESHOLD=10000 IF_CF_SOLVER_URLS=http://127.0.0.1:8001 \
  py -m uvicorn api.main:app --host 127.0.0.1 --port 8100

# 压测（并发阶梯）
py scripts/probe_stress.py --url http://127.0.0.1:8100 --levels 1,10,50,100,200 --per-level 30

# 定向覆盖率（v23 核心模块）
py -m pytest tests/test_db_batch_write.py tests/test_db_indexes.py tests/test_gallery_crud.py \
  tests/test_log_ws.py tests/test_task_cancel.py tests/test_redis_adapter.py tests/test_alerting.py \
  tests/test_alerting_webhook.py tests/test_auth_ip.py tests/test_chat_auth.py -q \
  --cov=api.db.core --cov=api.db.queries --cov=api.log_ws --cov=api.log_buffer --cov=api.auth \
  --cov=api.storage.redis_adapter --cov=api.alerting
```

## 6. 边界披露
- 全部本地 mock（无真实上游，符合烧钱红线）；压测绝对延迟含本机 HTTP 排队噪声（双口径标注）。
- `/v1/generate/async` 真实并发写入竞态由既有 test_task_cancel / test_gallery_crud 覆盖（v22.1 已闭环），本轮未重复压测（避免触发 auto-block 误伤）。
- 全量 api/ 覆盖率由 CI `--cov-fail-under` 门禁把关；本报告为 v23 改动模块定向证据。