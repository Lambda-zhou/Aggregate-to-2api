# Project Constitution — 听风AI (imagefree-2ai)

> Spec-Kit Phase 1 ｜ 2026-09-27 ｜ 治理原则，所有 v21 决策必须对齐本宪法。

## Core Values（核心价值）

1. **生产真实优先（Real-Production-First）**：本项目已在生产部署（imagefree.hwhcie.bond，Azure）。任何改动不得破坏现有可用性；"完成"必须由真实命令输出支撑，禁止理论可行/占位/mock 冒充闭环。
2. **一次调用即跑通（One-Call-Works）**：调用方、使用者、部署者按文档应能最少步骤成功；每个新增功能必须自带可复现的调用示例与验收命令。
3. **只追加不重构（Append-Only）**：不重写没坏的文件；不"顺手改进"相邻代码；复用既有轮子（compose profile / solver 联邦 / sw.js / manualChunks 均已存在），禁止重复造轮子。
4. **烧钱红线（Paid-API-Freeze）**：真实图片/视频生成、付费 LLM 上游调用默认预算 0；验证一律 `IF_MOCK_UPSTREAM=1` 或本地可逆资源，只有 agent/对话的已批 LLM 调用例外。
5. **诚实披露（Honest-Disclosure）**：受外部限制（R2 凭证/Grafana Cloud/UptimeRobot 账户/Docker 运行时）无法真测的项，必须标注"待验证/L2 授权"，不得伪装。

## Technical Principles（技术原则）

### Architecture（架构）
- 单实例 SQLite 形态保持；换 Postgres/上 MQ/分库分表当前是负优化（cf_solver 求解与上游生成才是真瓶颈）。
- 前后端契约以 OpenAPI / `task_to_public` / `_attach_explain` 既有结构为准，新字段走兼容追加。
- 配置统一 `IF_*` 前缀 + `api/config` 工厂 + `reset_settings()` 测试钩子；新字段同步 `.env.example` + `.env.production.example`。

### Code Quality（代码质量）
- 单测覆盖 ≥80%（核心路径）；文件 <800 行、函数 <50 行、嵌套 ≤4 层。
- 不可变优先：创建新对象而非就地修改；防隐藏副作用与并发竞态。
- 拆分含模块级常量的文件必须用 `_pkg_attr()`/`_mock_register()` 运行时读包命名空间（monkeypatch 契约）。
- Windows 平台：禁 `.sh`，用 node/PowerShell；命令链 `; if($?) { }`；搜索用内置 `rg`。

### Performance（性能）
- API 入口 ≤50ms 级（仅校验→入库→入队）；cf_solver 求解均时 6.13s 是硬瓶颈，改善靠多节点联邦。
- 前端主 chunk gzip <150KB（vite manualChunks 已存在，只测体积不重建）。
- SSE 连接数/画廊懒加载/画廊缓存需有界，防 OOM（512MB 容器）。

### Observability & Ops（可观测与运维）
- 所有关键路径有 Prometheus 指标（`imagefree_*` 前缀）+ 审计日志（trace_id）+ 告警。
- 数据抗毁分层：VACUUM INTO 每日全量（已闭环）→ litestream 秒级异地（v21 启用）→ 恢复演练（已闭环）。
- 版本号全链一致（~15 处 + e2e_v12 契约断言），防漂移靠版本门禁测试。

## Decision Framework（决策框架）

当做出技术决策时按序评估：
1. 是否对齐核心价值？（生产真实/一次调用/只追加/烧钱红线/诚实披露）
2. 是否有既有轮子可复用？（先 `rg`/graft 查项目内实现，再考虑新增依赖）
3. 是否可本机真实验证？（无 Docker/无外部凭证时选可逆替代验证，标注边界）
4. 长期维护成本是否最低？（清晰 > 炫技；小步改动 > 大重构）
5. 是否带 TDD 复现？（先写测试/复现，再实现，禁止先实现后补测）

## 适用范围与边界

- 本宪法约束 v21.0.0 迭代批次（B1-B7）全部改动与审计。
- 超出 v21 范围的长期建议（P3）只记录不实施。
- 冲突时优先级：安全/数据 → 用户明确验收 → 项目级规则 → 本宪法 → 默认行为。
