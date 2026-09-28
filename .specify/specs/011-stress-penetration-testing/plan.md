# Implementation Plan: 011 极限施压 + 防穿透测试落地

## Technology Stack
- 后端：FastAPI + uvicorn（测试实例：mock cf_solver :8001 + uvicorn :8100，本项目已有基础设施）
- 压测客户端：`scripts/probe_stress.py`（新，httpx 异步并发——不引 Locust/wrk，最小依赖符合宪法"只追加不造轮子"）
- 防穿透：既有 mock_cfsolver `/__fault` 注入 + HTTP 客户端
- 覆盖率：`pytest --cov=api`（既有 CI 口径）

## Architecture
无生产代码改动。新增 1 个压测探针脚本 + 报告文档 + 覆盖率缺口补测（若发现）。
验证全部在本地 mock 环境（mock solver + IF_MOCK_UPSTREAM=1），不触真实上游。

## Design Patterns
- 探针脚本：`asyncio.gather` + `Semaphore` 控制并发 → 与 `probe_federation.py` 同模式（既有先例）
- 防穿透断言：HTTP 状态码分类（429/4xx=优雅降级通过，5xx=失败标记）
- 报告：docs/research/stress-penetration-v23.md

## Security Considerations
- 全部本地回环（127.0.0.1），无外网面
- 不触真实付费上游（烧钱红线：mock 求解器）
- 压测不写生产 data/*.db（用 IF_DB_FILE=data/e2e_stress.db 隔离）

## Performance Strategy
- 并发阶梯：1/10/50/100/200 逐档，每档记 p50/p95/p99 + 错误率
- CSS/JS 不参与（读路径 API 压测，静态资源已由 nginx 缓存闭环）

## Error Handling
- 探针脚本超时 10s/请求，记录为 timeout 分类（非 5xx）
- 服务崩溃检测：进程存活 poll

## 任务分解与 TDD

| 任务 | 内容 | 依赖 | 验证 |
|---|---|---|---|
| T1 | `scripts/probe_stress.py`：并发阶梯压测读路径 | 无 | 脚本可跑（--url/--concurrency/--endpoints 参数化） |
| T2 | 防穿透注入断言（队列满/限流/SSE 超限/畸形 body） | T1 基建 | 每项 4xx/429 非 5xx |
| T3 | 全量覆盖率实测 → 缺口报告 | 后台 cov 已跑 | cov 报告 + 低覆盖模块清单 |
| T4 | 低覆盖关键模块补测 | T3 | 新增用例全绿，≥70% |
| T5 | 压测报告 docs/research/stress-penetration-v23.md | T1-T4 | 报告含数字+命令 |
| T6 | 独立审查 + 复验 | T5 | NEEDS_CHANGES → APPROVE |
| T7 | 版本/文档/报告收尾（若改代码则 bump 23.1.0） | T6 | 门禁全绿 + Release |

## 风险
- 测试机器并发排队污染均时（v23 N5 已证）→ 报告标注"本机 HTTP 排队噪声，读错误率为主要结论"
- 全量 cov 超 5min → 后台执行，缺口清单基于模块级聚合不逐字