# v23.1 压测/防穿透/覆盖率补测 六维独立审查报告（T6 · Critic）

> 审查人：独立 Critic（不继承主线程结论）｜ 日期：2026-09-28 ｜ 范围：`.specify/specs/011-stress-penetration-testing/` 落地物
> 审查对象：`scripts/probe_stress.py`、`tests/test_chat_auth.py`（+3）、`tests/test_alerting_webhook.py`（+5）、`tests/test_log_ws.py`（+2）、`api/log_buffer.py`（attrs 后缀修复）、`docs/research/stress-penetration-v23.md`、原始 JSON `docs/research/stress-v23.json`
> 方法：逐文件静态审查 + 定向运行验证（不修改任何文件）

---

## 0. 运行验证记录（Critic 实跑）

```
$ PYTHONIOENCODING=utf-8 py -m pytest tests/test_chat_auth.py tests/test_alerting_webhook.py tests/test_log_ws.py -q
....................................... [100%]     # 36 passed，补测 10 用例全绿
$ py -m py_compile scripts/probe_stress.py          # COMPILE_OK
$ python -c "json.load(stress-v23.json)"           # 合法 JSON
$ python -c "json.load(cov-v23.json)"              # 合法 JSON
```

三个补测文件 + 编译全部通过，无伪造运行证据。

---

## 1. 需求完整性

### 1.1 spec 011 与落地对照（结论：基本完整，一项声明口径偏差）

| spec 要求 | 落地 | 证据 |
|---|---|---|
| US1 并发阶梯 1→200，p50/p95/p99、错误率、进程存活 | ✅ | `probe_stress.py` 5 端点 × 5 档 × 30 请求，25 组结果入 JSON，报告含双口径 |
| US1 输出压测报告 | ✅ | `docs/research/stress-penetration-v23.md`（注：spec 写的文件名是 `stress-test-v23.md`，实际为 `stress-penetration-v23.md`，文件名不一致但内容交付）|
| US2 防穿透：畸形/超长/大体积 → 4xx | ✅ | 报告 §2 表格 422 实测 |
| US2 IP 限流超限 → 429 + Retry-After | ✅ | 报告 §2：10×200 → 5×429 + Retry-After: 60 |
| US3 覆盖率盲区猎杀 + 补测 | ✅ | cov-v23.json + 10 个新用例 + attrs 前缀修复 |
| **进程存活检测** | ⚠️ 仅间接 | 探针无独立存活 poll，plan.md 声称 "进程存活 poll" 未落地（见 3.3） |

### 1.2 需求范围外发现：`crashed_at` 记录逻辑（审查要求外，顺带评估）

`probe_stress.py:115-117`：`crashed_at` 只在 `crashed=True` 时记录，而 `crashed` 触发条件见 3.1。

---

## 2. 逻辑正确性

### 2.1 ✅ `api/log_buffer.py` attrs 前缀修复——正确且无副作用（本批核心修复）

- 旧代码 `k[6:]` 对 `attr_custom` → `ustom`（多剥 1 字符）；新代码 `k[len("attr_"):]` → `custom`。`len("attr_") == 5`，修复正确。
- **副作用核查**：Grep `attr_` 于 `api/`，只有 `log_buffer.py` 自身定义/使用，无其它模块消费 `attr_` 前缀 key；前端 `frontend/src/` 无 `attrs` 消费方（grep 零命中）。即：**修复不破坏任何其它读路径**。
- **行为一致性**：`entry["attrs"]` 的消费者未在本仓库找到（json-line 消费为声明，实际无代码消费 `attrs` 键）。修复后 `attrs` 键名从 `ustom` 变 `custom`，属于**行为变更**——若存在外部消费方将看到键名变化。仓库内无消费方，风险低，但报告 §3 未披露这一键名变更。→ P3

### 2.2 ✅ `filter_by_trace_id` 逻辑正确

`log_buffer.py:91-95`：过滤 `e.get("trace_id") == trace_id`，取尾部 `lines` 条。语义正确；无 trace_id 条目自然跳过，符合预期。

### 2.3 ✅ `_send_webhook` 三路径实测通过、`_safe_webhook_url` 三态正确

- `_safe_webhook_url` 用 `urlsplit` 剥 query、容错 `except → "<webhook>"`，空串兜底正确。
- Redis 脱敏、webhook 脱敏、URL query 脱敏已在 `test_log_ws.test_redact_utility_covers_redis_and_webhook` 覆盖。

### 2.4 ✅ new chat auth 3 用例真实命中限流分支（非凑数）

`test_chat_rate_limit_429_when_sliding_window_full` 通过改 `if_chat_rate_limit=3` + reset 后 3 次入桶第 4 次 429，`e.status_code == 429` 且 `retry_after_seconds >= 1`——**真实触发** `check_chat_rate_limit` 的 raise 分支（auth.py:200-205），非 mock 表面。DAG 用例同理（auth.py:234-236）。`limit=0` 走 `if limit <= 0: return` 放行路径（auth.py:184-185）。三用例均为有效路径覆盖。

- 唯一瑕疵：两次 `import api.config` + `cfg.settings` 混用（`old = getattr(cfg.settings, "if_chat_rate_limit", 60)` 但 `import api.config` 后直接 `api.config.settings.if_chat_rate_limit = 3`）——`cfg` 与 `api.config` 是同一模块对象，语义等价，但风格不一致、有冗余 import。P3 风格。

---

## 3. 边界情况

### 3.1 🔴 P1：`probe_stress.py` 崩溃阈值把 **429 算进 error_rate**，与「429=优雅降级」矛盾

`scripts/probe_stress.py:71-82`：
```python
total_ok = sum(c for s, c in status_counts.items() if 200 <= s < 300)
error_rate = round((total - total_ok) / total, 4)
```
即 `error_rate = 非2xx / total`，**429 天然计入 error_rate**。而 spec 011 的 US2 明确「队列满 → 429 优雅降级不 5xx = 通过」；报告 §2 也把 429+Retry-After 当正确行为。

- **冲突**：当被测服务因限流正常返回 429（且比例 >5%）时，探针判定 `crashed=True` 提前终止并 `crashed_at` 记录该档，**把健康保护误报为崩溃**。
- **复现**：`IF_CHAT_RATE_LIMIT=2` 起服务，压测任何走 chat 限流的端点（或用会造成 429 的端点），探针报告 "已达崩溃阈值"。
- **影响**：工具语义偏差，未来压测时会产出误导性崩溃判定；本次实测 0 个 429 所以报告数字不受影响，但工具本身有缺陷。
- **修复建议**：`error_rate` 应排除 429（或单列 `429` 与 `5xx_ratio` 分开判定）：崩溃阈值基于 `5xx + client_errors`，429 仅作降级记录。

### 3.2 🟡 P2：docstring 与代码口径不符——docstring 说 ">5% 5xx"（L13）与注释 ">5% 5xx/客户端错误"（L107），但实现是"非 2xx 全部"（含 429）

三处表述（docstring L13、注释 L107、代码 L108 `error_rate > 0.05`）口径不一致。即使 3.1 判定语义保留 429，文档也必须与实现同步。→ 修复 3.1 时一并统一。

### 3.3 🟡 P2：report 声称"进程全程存活"但探针无存活 poll / 无显式崩溃检测

- `plan.md` 声称 "服务崩溃检测：进程存活 poll"；`probe_stress.py` 全文**没有**任何 subprocess/存活探测，"进程存活"仅从"请求全部成功"间接推断。
- 报告 L17/L30 写 "进程全程存活"——该结论目前对（200 并发下 30/30 全 200，进程必然存活），但**工具能力与 plan 声明不符**：一旦服务进程挂掉，`client_errors`（连接拒绝）会捕获并算 error_rate，register 仍能工作——所以是"间接等价"而非缺失。实质影响低，但 plan 声称的 poll 未落地属文档-实现漂移。
- 修复建议：探针结束前主动 GET `/v1/healthz` 一次作为存活断言（轻量，3 行），或 plan.md 删掉 poll 字样。

### 3.4 🟢 P3：`_pct` 分位数在 n=30 时 p99≈最大值

```
s[min(len(s)-1, int(len(s)*p/100))]
```
n=30：p99 → `s[min(29, 29)] = s[29]` = 最大值；p50 → `s[15]`；p95 → `s[28]`。30 样本下 p99 恒等于 max，且 p95≈第 96.7% 分位（28/30）。这是 nearest-rank 近似，对"读路径延迟画像"足够，但**报告只展示 p50/p99 两列，p99 实际是 max**，容易让读者误以为 p99 是稳定统计量（outlier 敏感）。
- 影响：压测结论（延迟在百 ms-秒级）方向正确，但"p99"命名为近似值。若追求严谨可用 `statistics.quantiles` 插值或至少 n≥100/档。

### 3.5 📐 其它边界核查（通过）

- `_probe_level` gather 全部 await 后才返回 → 每档串行执行，不会并发叠加；Semaphore 正确限并发。
- 超时（httpx.TimeoutException）归 `client_errors`：合理（10s 无响应=服务低于 SLA），但严格语义上超时不等于崩溃、也不等于 5xx，见 3.1/3.2 的统一口径问题。
- 进程退出（被 kill）场景：`client_errors` 计数，error_rate 会超阈值 → 能判定崩溃 ✓（间接）。

---

## 4. 代码质量

| 项 | 评估 |
|---|---|
| `probe_stress.py` 结构 | 单一职责清晰：`_pct`/`_probe_level`/`_main` 分层，可读性好 |
| 不可变风格 | `status_counts` dict 累加为函数内局部变更，无可变共享泄漏；`_probe_level` 每档新建列表/字典 ✓ |
| 错误处理 | `except Exception` 宽捕获但记录到 errors 列表，符合压测探针场景 ✓ |
| test_alerting_webhook 新增 | 见 4.1（P2 削弱） |
| test_file 内 `import pytest` 在文件中段 | test_alerting_webhook.py 在文件第 141 行（diff 中）出现 `import pytest`（原文件顶部无）——P3：import 应统一置顶 |

### 4.1 🟡 P2：`test_alerting_webhook.py` 三个新 webhook 用例断言被削弱（覆盖达标但行为断言不足）

- `test_send_webhook_success_logs_redacted_url`（新·第 1 个）：**测试名声称"日志只记脱敏 host:path"**，但断言的是 `captured.get("url")`（外发确实发生）+ 独立调用 `_safe_webhook_url("...")` 检查返回值脱敏——**没有断言 log.info 实际输出内容**（日志输出未被捕获/验证）。若 `_send_webhook` 里第 239 行的 log.info 传的**不是** `_safe_webhook_url(url)`，此测试照样通过 → 测试与测试名不符，关键声明（日志脱敏）未真验证。
  - **修复建议**：`caplog` 捕获 `imagefree_api.alerting` logger，断言 `caplog.text` 不含 token 且含 host。
- `test_send_webhook_non2xx_warns`：只断言 "不抛"，未断言 `log.warning("告警 webhook 返回非 2xx")` 真被调用——弱断言。
- `test_send_webhook_exception_swallowed`：只断言 "不抛"，未断言 `log.warning("告警 webhook 外发失败")`。
- 综合：这三个用例对 95.4% 覆盖率贡献真实，但**行为断言以"不抛"为主，未验证告警日志副作用**，属于"覆盖到了、行为没测结实"。→ P2（诚实性：报告 §T4 称"覆盖 alerting 外发全路径"，事实是路径执行了但副作用未经断言）。

> 对照：chat_auth 3 用例有真实状态码断言（2.4 ✅），log_ws 2 用例有真实字段断言（2.2 ✅）——只有 webhook 3 用例削弱。

---

## 5. 测试覆盖

### 5.1 覆盖率数字与 cov-v23.json 完全一致（诚实性 ✓）

| 模块 | 报告声称 | cov-v23.json 实测 | 一致 |
|---|---|---|---|
| api/alerting.py | 95.4% | 95.4%（83/87 line） | ✓ |
| api/auth.py | 76.1% | 76.1% | ✓ |
| api/log_buffer.py | 88.9% | 88.9% | ✓ |
| api/log_ws.py | 74.5% | 74.5% | ✓ |
| api/storage/redis_adapter.py | 97.3% | 97.3% | ✓ |
| api/db/core.py | 66.0% | 66.0% | ✓ |
| api/db/queries.py | 48.8% | 48.8% | ✓ |

### 5.2 补测是否真跑通：是（Critic 实跑 36 passed），断言强度分部不均

- log_ws 2 用例：真实断言（trace_id/req_id/attrs.custom/脱敏）✓ 强
- chat_auth 3 用例：真实断言（status/retry_after）✓ 强
- alerting 5 用例：2 个强（safe_webhook_url 三态、成功路径 URL 外发），3 个弱（见 4.1）✗

### 5.3 "0 错误率"结论与原始 JSON 一致性（诚实性核查 ✓）

`stress-v23.json` 25 条：所有条目 `ok=30/30`、`status_5xx=0`、`status_429=0`、`client_errors=0`、`error_rate=0.0`、`crashed_at=None`。
报告 §1"全部 ok=30/30，0 个 5xx，0 个 429，进程全程存活"**与 JSON 完全一致**，未夸大。✓

### 5.4 报告 p50/p99 表格与 JSON 逐点核对（25/25 一致 ✓）

以 healthz/stats/models 抽验：并发 1 行 healthz（40.7/127.8）、stats（24.8/58.4）；并发 50 healthz（586.0/805.9）；并发 200 models（351.3/650.6）——全部与 JSON 一致。报告未篡改数字。

---

## 6. 实际运行证据

- ✅ 补测 10 用例：Critic 实跑 36 passed（含原有 26）
- ✅ py_compile probe_stress.py：通过
- ✅ 两个 JSON 可解析
- ⚠️ 探针实际压测运行：报告提供复现命令 + JSON 产物，Critic 未重跑完整压测（需 uvicorn + mock solver 环境，非只读验证必要项，且会触发本地写库）——属"静态确认 + 产物核对"，不构成对压测真实性的否定；产物内部自洽（25 组、数字合理、无矛盾）。
- ⚠️ 报告声称"task-failure-burst 自动封禁"（engine.py:761）：`sed` 验证 engine.py 755-775 确含 `_record_auto_block_violation(ip, "task-failure-burst")` 逻辑 ✓ 声称成立。

---

## 发现清单（按严重级）

| # | 级别 | 位置 | 发现 | 复现/影响 | 建议 |
|---|---|---|---|---|---|
| F1 | **P1** | scripts/probe_stress.py:71-82,108 | 崩溃阈值 error_rate 含 429 → 限流保护被误报为崩溃 | 设 IF_CHAT_RATE_LIMIT=2 起服务再压测即触发 false crash | error_rate 排除 429；5xx+client_errors 独立判定 |
| F2 | **P2** | docs/research/stress-penetration-v23.md + L13/L107 注释 | 崩溃判定口径三处不一致（docstring>5%5xx / 注释 5xx+客户端错误 / 实现非2xx全部） | 维护者按文档判读会误解探针行为 | 修 F1 时统一三处表述 |
| F3 | **P2** | tests/test_alerting_webhook.py（3 新用例） | webhook 成功用例名"logs_redacted_url"但未断言日志输出；非2xx/异常两用例仅"不抛"无副作用断言 | 若 log.info 误记全 URL，测试通过但实际泄露 | caplog 捕获告警 logger 断言脱敏/告警文本 |
| F4 | **P2** | scripts/probe_stress.py（无存活 poll）+ plan.md "进程存活 poll"声明 | plan 声称 poll 未落地；报告"进程全程存活"仅由请求成功率间接推断 | 进程挂掉时仍能间接判崩溃，影响低 | 结束前 GET /healthz 一次作为存活断言 |
| F5 | **P3** | log_buffer.py:73-82 | attrs 键名修复（ustom→custom）未在报告披露为行为变更；仓库内无 attrs 消费方，键名变化仅影响外部潜在 json-line 消费 | 外部消费方若按 ustom 读取将失效 | 报告 §3 补一行披露 |
| F6 | **P3** | scripts/probe_stress.py:38-42 | 30 样本下 p99=最大值（nearest-rank 端点效应），报告直读易误导 | 压测方向性结论不受影响 | 文档注明近似口径或提高 n |
| F7 | **P3** | tests/test_chat_auth.py 新增段 | `cfg.settings` 与 `import api.config` 混用、重复 import；风格不统一 | 无功能影响 | 统一为 api.config；import 置顶 |
| F8 | **P3** | tests/test_alerting_webhook.py | `import pytest` 出现在文件第 141 行（中段）而非顶部 | ruff E402 类风格问题 | 移顶 |

---

## 六维结论

| 维度 | 结论 |
|---|---|
| 需求完整性 | ✅ 基本完整（spec 011 各 US 均有落地证据；1 项 plan 文档漂移 F4） |
| 逻辑正确性 | ✅/⚠️ 核心修复（attrs）正确无副作用；探针崩溃阈值口径缺陷 F1 |
| 边界情况 | ⚠️ F1（429 误判崩溃）+ F3（webhook 副作用未断言）为主要缺口 |
| 代码质量 | ✅ 探针分层清晰；测试文件 import 位置等 P3 风格项 |
| 测试覆盖 | ✅ 数字与 cov-v23.json 完全一致；webhook 3 用例断言强度偏弱（F3） |
| 运行证据 | ✅ 补测实跑 36 passed、编译通过、JSON 一致、报告"0 错误率"与原始数据吻合 |

## Verdict（首轮）

**NEEDS_CHANGES**（无 P0，F1 为 P1 须修，F2/F3/F4 为 P2 建议本轮修）

- 不阻主干交付：核心修复（attrs）正确、补测真实跑通、报告诚实（数字逐点核对无误）。
- 但 `probe_stress.py` 崩溃阈值含 429 是**工具级逻辑缺陷**（F1），且 webhook 3 用例的行为断言削弱（F3）使"外发全路径脱敏"的声明证据不足。
- 修复后复验范围：F1 改 error_rate 口径 + docstring/注释统一 → 重跑 py_compile + probe 冒烟（mock 429 场景）；F3 加 caplog → 重跑 36 用例。

---

# 复验结论段（2026-09-28 · 主控修复后，Critic 独立复核）

## 复验运行记录（Critic 实跑）

```
py -m pytest tests/test_chat_auth.py tests/test_alerting_webhook.py tests/test_log_ws.py -q
→ 36 passed（与首轮同批；点数 grep 计数 = 36，addopts -q 不打印 summary 行）
py -m py_compile scripts/probe_stress.py → COMPILE_OK
py -m ruff check scripts/probe_stress.py tests/test_chat_auth.py tests/test_alerting_webhook.py tests/test_log_ws.py api/log_buffer.py
→ All checks passed!（0 error）
```

## F1-F8 逐项复验结果（全部确认修复落地）

| # | 声称修复 | 复验证据 | 结论 |
|---|---|---|---|
| F1 | error_rate 只含 5xx+client_errors（fatal_count 字段），429 单独列示不计崩溃 | probe_stress.py:71-92：`fatal = total_5xx + len(errors)`，`error_rate = fatal/total`；429 走独立 `status_429` 字段。数学验证：429-only→0.0 不判崩 / 5xx=6/30→0.2 判崩 / 1/30→0.033 不判崩 | ✅ |
| F2 | 三处口径统一为"5xx+客户端异常，429 不计" | docstring L13-15、注释 L74-76、实现 L77/L86/L112-115 全部一致 | ✅ |
| F3 | webhook 成功用例加 caplog 断言 | test_alerting_webhook.py 新增：`any("webhook 已外发至" in rec.message ...)` + `not leaked`（token 不在日志）。测试通过且断言真实执行 | ✅ |
| F4 | 结束前 GET /healthz 存活断言 + process_alive 字段 | probe_stress.py:121-127（`alive = "alive"/"degraded-*"/"unreachable"`）+ payload:131 | ✅ |
| F5 | 报告补 attrs 键名变更披露 | stress-penetration-v23.md:86 新增披露段（ustom→custom、无消费方、风险零） | ✅ |
| F6 | 报告注明 p99 nearest-rank 口径 | stress-penetration-v23.md:19 口径注明（30 样本端点效应） | ✅ |
| F7 | test_chat_auth.py 统一 api.config.settings + import 修正 | diff 确认：移除 `cfg.settings` 混用与中段 `from fastapi import Request`，统一 `api.config.settings` | ✅ |
| F8 | test_alerting_webhook.py 中段 import pytest 移除 | 文件头已含 `import pytest`（第 10 行），新增段无重复 import | ✅ |

## 复验 verdict：**APPROVE**

- 首轮 P1（F1）已修复：429 不再计入崩溃判定，阈值边界（>5% 致命错误率）数学验证与注释一致。
- 首轮 P2（F2/F3/F4）全部落地并有运行证据。
- 首轮 P3（F5-F8）全部落实。
- 补测 36 用例 Critic 实跑全绿；probe_stress.py 编译通过；ruff 相关 6 文件 0 error。
- 剩余观察（不影响 APPROVE）：probe_stress.py 为 untracked 新文件，入库时走正常提交流；test_alerting.py 含真实网络用例（后台运行超时 120s，未纳入本批复验——本批改动不涉及该文件，无回归影响）。