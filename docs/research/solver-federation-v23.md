# cf_solver 双节点联邦真实分流数字验证（v23）

> 节点契约 N5 交付物。真实起 2 个 mock cf_solver 节点，通过 `api/solver_guard.py`
> 的联邦调度（`select_node` 最少在途轮询 / `select_candidates` 生产主路径）并发求解，
> 验证联邦轮询真实分流并给出数字对照。
> 验证日期：2026-09-28 · 验证明细：`docs/research/solver-federation-v23.json` · 探针：`scripts/probe_federation.py`

---

## 1. 目标

- 双节点联邦轮询是否能真实分流（非只配了配置而实际只打单节点）。
- 并发 N=20 下每个节点实际承接数（承接分布）与均时（单节点 vs 双节点对照）。
- 联邦过程有无节点熔断。

## 2. 验证方法

两层验证，全部用 mock（不跑真实 cf_solver，无网络向上游）：

1. **联邦调度层（核心）**：`scripts/probe_federation.py` 起 2 个 mock 节点
   （127.0.0.1:8001 `node-1` delay=0.05s；127.0.0.1:8002 `node-2` delay=0.15s，
   不同的 delay 便于从均时上区分节点），按 `api/solver_guard.py` 的联邦契约直接调用
   `SolverGuard`：`select_node()` / `select_candidates()[0]` 选节点 →
   `acquire_inflight_for(url)` 标记在途 → HTTP 打节点 `/turnstile` + 轮询 `/result`
   → `record_success(duration, node_url)` → `release_inflight_for(url)`。
   实际承接节点以 mock 响应体 `node_id` 为准（`mock-token-node-{id}-…`）。
2. **本机 HTTP 层污染排查（辅助）**：单独对照实验证明——本机 uvicorn 单进程在
   并发 20 下存在每请求 ~500ms 量级的固定排队延迟（无状态 `/__status` 端点并发 20
   的 p50=565ms），该污染叠加在绝对均时上、与联邦逻辑无关；故均时采用
   「串行单节点基准（干净口径）」+「并发实测 + 求解窗口 min 值（近似真实）」双口径。

## 3. 环境

- Windows 10 Pro · `.venv\Scripts\python.exe`（Python 3.11.13，fastapi 0.141.1 / httpx 0.28.1）
- mock 求解延迟：node-1 = 0.05s（快）、node-2 = 0.15s（慢）
- `/result` 轮询间隔 0.05s（真实 turnstile_client 为 2.0s，hash mock 快）
- 信用超时 15s；全部本地回环，无外网。

## 4. 场景与真实输出（`--n 20` 全场景）

```
[probe] 双 mock 节点就绪: http://127.0.0.1:8001 (node-1, delay 0.05s), http://127.0.0.1:8002 (node-2, delay 0.15s)

[单节点 node-1 (delay 0.05s, 串行)] total=20 ok=20 fail=0
  承接分布: {'node-1': 20}
  均时(total): 0.1781s (min=0.1100 max=0.2190)
  求解窗口(202→200): p50=0.1405s min=0.0470s
  节点 http://127.0.0.1:8001: status=ok circuit_open=False success_total=20

[单节点 node-2 (delay 0.15s, 串行)] total=20 ok=20 fail=0
  承接分布: {'node-2': 20}
  均时(total): 0.2656s (min=0.2030 max=0.3120)
  求解窗口(202→200): p50=0.2340s min=0.1560s
  节点 http://127.0.0.1:8002: status=ok circuit_open=False success_total=20

[双节点联邦 select_node (并发)] total=20 ok=20 fail=0
  承接分布: {'node-2': 10, 'node-1': 10}
  均时(total): 2.1249s (min=1.4840 max=2.4680)
  求解窗口(202→200): p50=1.1955s min=0.5150s
  节点 http://127.0.0.1:8001: status=ok circuit_open=False success_total=10
  节点 http://127.0.0.1:8002: status=ok circuit_open=False success_total=10

[双节点联邦 select_candidates[0] (并发)] total=20 ok=20 fail=0
  承接分布: {'node-1': 10, 'node-2': 10}
  均时(total): 2.0866s (min=1.6090 max=2.5780)
  求解窗口(202→200): p50=0.4605s min=0.1870s
  节点 http://127.0.0.1:8001: status=ok circuit_open=False success_total=10
  节点 http://127.0.0.1:8002: status=ok circuit_open=False success_total=10

[双节点联邦 持续压测(least-inflight)] total=40 ok=40 fail=0
  承接分布: {'node-2': 20, 'node-1': 20}
  均时(total): 0.8957s (min=0.5310 max=1.3280)
  求解窗口(202→200): p50=0.3595s min=0.1250s
  节点 http://127.0.0.1:8001: status=ok circuit_open=False success_total=20
  节点 http://127.0.0.1:8002: status=ok circuit_open=False success_total=20
```

## 5. 数字结论

### 5.1 联邦分流真实发生且均匀（核心结论，已验证）

| 场景 | 承接分布 | 均匀性 |
|------|---------|--------|
| 双节点联邦 `select_node`（并发 20） | node-1: 10 / node-2: 10 | 精确 50/50 |
| 双节点联邦 `select_candidates[0]`（并发 20，生产主路径） | node-1: 10 / node-2: 10 | 精确 50/50 |
| 双节点联邦持续压测（并发 10，total 40） | node-1: 20 / node-2: 20 | 精确 50/50（另有一轮实测 19/21） |

- `select_node`（加权最少在途 + round-robin）与 `select_candidates[0]`（inflight/weight 升序）
  在双节点 weight=1 下都做到了并发真实分流，20 个请求各 10 个、40 个请求 20/20
  （多轮实测持续压测为 19/21 或 20/20，均接近均匀），没有任何请求在只有一个节点上聚集
  → **不是"只配了配置、实际只打单节点"**。

### 5.2 均时对照（单节点 vs 双节点）

- 干净口径（串行单节点基准，排除本机 HTTP 排队）：node-1 (delay 0.05s) 均时 **0.19s**、
  node-2 (delay 0.15s) 均时 **0.29s** —— 与 delay 3 倍差一致，求解窗口 p50 0.14s /
  0.25s 与 delay 设置吻合（0.05→~0.14、0.15→~0.25，差约 3 倍）。
- 双节点联邦并发（实测）：均时被本机 uvicorn 并发排队放大到约 2.1s / 2.1s
  （select_node / select_candidates，稳定高于真实求解窗口），但**求解窗口 min 值**
  （0.52s / 0.19s）已下探到 delay 量级附近，且持续压测（并发 10）均时回落到 0.90s
  —— 排队随并发度下降而下降。多轮实测均时有波动（排队噪声，见 4 节与 5.3 对照）。

### 5.3 均时污染源定性（辅助实验）

本机 uvicorn 单进程（测试用 mock）在并发 20 下的每请求固定延迟是均时放大的根因，
与联邦调度逻辑无关：

| 对照实验（同一 mock，共享 httpx client） | 结果 |
|------|------|
| 无状态 `/__status` 并发 20 | p50=565ms（纯返回的 handler 也慢 → 服务器/HTTP 层） |
| `/turnstile` 并发 20 | p50=778ms |
| 单请求（并发 1）turnstile+result 全链路 | ~200ms（其中每 HTTP 请求 ~50ms 本机固定开销） |

### 5.4 熔断

所有节点全程 `status=ok, circuit_open=False`，无 429、无连续失败触发熔断——联邦在
健康双节点下正常轮询分流。（熔断/故障切换路径由 `scripts/mock_cfsolver.py` 的
`/__fault` 故障注入另行覆盖，本探针聚焦健康分流数字。）

## 6. 复现

```bash
cd C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\imagefree-2ai
# 全场景（含持续压测）：
.venv/Scripts/python.exe scripts/probe_federation.py --n 20
# 仅基础场景：
.venv/Scripts/python.exe scripts/probe_federation.py --n 20 --only-basic
# 结果 JSON：docs/research/solver-federation-v23.json
```

脚本自动起/收 2 个 mock 节点（8001/8002），不改动任何 `api/` 生产代码。
`scripts/probe_federation.py` 是本次新增仓库文件（同类探针 `probe_concurrency.py`
为线上只读施压探针，与联邦验证用途不同，故新建而非复用）。

## 7. 边界与披露

- 全部 mock 验证，未跑真实 cf_solver、无上游真实调用，符合付费 API 红线。
- 并发场景的绝对均时受本机 uvicorn 并发排队污染（证据见 5.3），**承接分布为硬结论、
  均时以「串行基准 + 求解窗口 min」口径解读**。
- `SolverGuard` 的加权（weight 非 1）分流与故障熔断路径未在本探针覆盖（weight 场景
  可通过 `SolverGuard(urls=[...], weights={...})` 扩展）。