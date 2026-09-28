# Feature Specification: 011 极限施压 + 防穿透测试落地（010 US2 兑现）

## Problem Statement

`.specify/specs/010-production-hardening-ngx/spec.md` 定义了 US2「高并发极限施压探针」：
> 作为站长，知道系统在多少并发下会崩——压测 /v1/healthz + /v1/gallery（只读）不同并发，
> 记录瓶颈：CPU/内存/延迟/错误率，产出压测报告。

**但 010 只有 spec.md，无 plan.md/tasks.md，US2 从未真实执行** —— 这是"承诺了但未闭环"的直接反例。
同时 v23 覆盖率抽查（db.core/log_ws/log_buffer）仅 65% < 70% fail-under——「覆盖率看着有 80%，
一上线还是出 bug」的根因是**覆盖率是总量，关键路径/边界未覆盖**。

用户点名「极限施压与防穿透测试（极端情况下系统会不会崩溃、数据会不会错乱）」——本轮必须真实落地。

## 现状（verification-log 已闭环，不重做）
- US1 nginx 静态缓存/gzip：SOP §1 记录 v20.3.2 已配（✅ 闭环）
- US3 契约防坑：v23 N1 已审计 50 端点（✅ 约 80% 覆盖，错误码/分页/状态枚举已核对）
- solver 双节点联邦分流：v23 N5 已实测（✅）
- SQL 慢查询猎杀/索引：v23 N2 已闭环（✅）
- 日志脱敏/鉴权：v23 N3 已闭环（✅）

## User Stories

### US1: 读路径极限施压（P0）
作为站长，知道只读端点（/v1/healthz、/v1/gallery、/v1/stats、/v1/providers、/v1/models）在多少并发下
延迟劣化/开始 5xx/进程崩：
- 并发阶梯 1→10→50→100→200（或直至失败），记录 p50/p95/p99 延迟、错误率、进程存活
- 输出压测报告（docs/research/stress-test-v23.md），标注"服务框架瓶颈 vs 上游瓶颈"（宪法 §Performance：API 入口 ≤50ms 级）

### US2: 防穿透/极端注入（P0）
作为攻击者，验证极端输入不崩：
- 队列满：IF_MAX_QUEUE 压满 → 429 优雅降级不 5xx
- IP 限流超限：连打 → 429 + Retry-After 头
- SSE 连接超限：IF_SSE_MAX_CONNECTIONS 压满 → 429 断连
- 超大 body / 畸形 JSON / 超长 prompt → 4xx 不 500、不 OOM
- 并发写入竞态：同任务双 cancel / 双 retry / 幂等 key 重放 → 状态一致

### US3: 覆盖率盲区猎杀（P1）
作为审计员，找到"总量达标但关键路径漏测"：
- 全量 --cov 实测，列出 <70% 的 api/ 子模块
- 对高风险低覆盖模块（db.core 批量写、solver_guard 联邦、auth 限流、log_ws 广播）补关键路径用例
- 输出覆盖率缺口报告 + 补测

## Success Metrics
- 压测报告含并发阶梯表 + 瓶颈定位 + 建议（可复现命令）
- 防穿透每项注入后服务存活、错误为 4xx/429 非 5xx
- 关键子模块覆盖率 ≥70%（全量 fail-under 达标）

## Out of Scope
- 真实上游付费调用（烧钱红线：用 mock solver + IF_MOCK_UPSTREAM=1）
- DB Replication/Sharding/MQ（宪法：单机形态负优化）
- 已闭环项重做（US1/US3 仅回填证据）
