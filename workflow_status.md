# workflow_status — v21.0.0 终局闭环（001-v21-ar-closure）

> 更新：2026-09-27 ｜ 规范来源：`.specify/specs/001-v21-ar-closure/`（constitution/spec/plan/tasks）｜ 蓝本：`计划书/下一步改进指南.md`
> 规则：只记事实与证据；只有观察到交付物+验收证据才标记 done；外部受限项标注"待验证/L2 授权"不伪装。

## 任务契约（验收标准表）

| ID | 任务 | 验收标准 | 状态 | 证据 |
|---|---|---|---|---|
| T1 | test_version_consistency.py | 全链读取比对 + 当前应红（20.3.10 vs 20.3.11 / e2e 20.0.0） | ✅ 已闭环 | pytest 4 passed（3 原用例 + H1 文件存在断言） |
| T2 | scripts/sync_version.py | --set/--check/--dry-run 幂等 | ✅ 已闭环 | dry-run/check/set 全验证；GBK reconfigure |
| T3 | 测试隔离复现 + 修复 | 两实例独立状态，组合跑 0 污染 | ✅ 已闭环（v20.3.11 fixture 已修） | 组合跑 39 passed |
| T4 | DLQ i18n | zh/en dlq.* key 平行 + DLQ.tsx 中文硬编码 0 | ✅ 已闭环 | i18n 测试 8/8 + vitest 296/296 |
| T5 | PWA manifest/icon/组件 | manifest.webmanifest + icon + link + iOS meta + PwaInstallPrompt.vue | ✅ 已闭环（含 B1 修复） | landing build 成功，dist 含全部 PWA 产物 |
| T6 | litestream 启用路径 | .env.production.example 六变量 + SOP §3 小节 + type:local 验收命令 | ✅ 已闭环（R2 凭证 L2 待授权） | 模板位齐全 |
| T7 | uptime_probe.py + 观察栈命令 | 脚本语法可跑 + 文档含 compose obs 验证命令与真实指标名 | ✅ 已闭环（含 B2 修复） | py_compile 过；无服务 exit 1 |
| T8 | SEO（sitemap/robots/JSON-LD/og） | landing/public 文件存在 + index.html 引用 | ✅ 已闭环 | 入 landing dist |
| T9 | 旧产物处理 | 决策记录 + 清理或保留标注 | ⚠️ 已记录（含安全项） | 见 §旧产物处理决策 |
| T10 | 版本统一 21.0.0 + dist 重建 | sync --check 全绿 + landing/frontend dist 重建 | ✅ 已闭环 | 全链 21.0.0；双 dist 重建成功 |
| T11 | 独立审查线程 | 需求/逻辑/边界/质量/覆盖/运行 6 维审查 + 修复清单闭环 | 🔄 复验中 | 首轮 NEEDS_CHANGES（B1/B2 已修）；复验线程 a2c7df1c83432599f |
| T12 | HTML 报告 + 测验 | 上下文/直觉/变更/测验生成 | ⏳ 待复验通过后 | |
| T13 | 沉淀 skills/workflow | .claude/skills 或文档化工作流 + 记忆更新 | ⏳ 待复验通过后 | |

## 任务图（依赖）

```
T1 ──► T2 ──► T10（版本统一）
T3（独立）          T9（独立，收尾）
T4（前端静态）       T5（前端静态）
T6 ──► T7（运维工具，并行）
T8（前端静态）
T10 ──► T11（独立审查）──► T12（报告）──► T13（沉淀）
```

## 验证日志（命令 + 结果）

- 2026-09-27 v21 后端：`pytest tests/test_version_consistency.py` → **3 passed 全绿**（经 sync_version 统一 21.0.0 后）；`test_account_pool.py + fsm + predict_exhaustion` 组合 **39 passed**（隔离债已在 v20.3.11 修复）；`test_sse_events_unit.py` 20 passed；`test_agent_routes.py` 11 passed
- 2026-09-27 前端：`vitest run --pool=threads --testTimeout=20000` → **28 files / 296 tests 全绿**（含 DLQ i18n）；`landing npm run build` 23.6s 成功含 PwaInstallPrompt；`frontend npm run build` 33.4s 成功（index gzip 84.04kB < 150KB）
- 2026-09-27 版本全链：`sync_version.py --check` → 全链一致 21.0.0（9 文件 + e2e 契约）；manifest JSON 校验合法
- 2026-09-27 PWA 产物：`landing/dist/` 含 manifest.webmanifest + pwa-192/512 + apple-touch-icon + sitemap + robots + sw.js(v21.0.0 三策略保留)
- 2026-09-27 测试 A：`pytest tests/test_version_consistency.py` 3 passed；`test_account_pool + 3` 39 passed

## 审查发现（Critic 独立）

- （待填）

## 阻塞项

- 前端 vitest：`frontend/node_modules` 缺失 → 前端改动用静态验证 + 语法检查替代，verification-log 标注"依赖装齐后跑 vitest"。（已解除：node_modules 实际存在，vitest 296/296 已实跑）
- litestream R2 / Grafana Cloud / UptimeRobot = 外部资源 → 本期只文档化路径，真接需 L2 授权。
- 前端 dist 重建需要 node_modules → 若缺失仅生成文件不 build，标注待构建。（已解除：双 dist 重建成功）

## 旧产物处理决策（T9，2026-09-27）

| 文件 | 内容 | 决策 |
|---|---|---|
| `_dep33.py` | 生产 SSH 运维脚本（含**明文凭证**：IP/root/密码） | ⚠️ **安全项**：未跟踪 git 但常驻根目录；建议移入 `.gitignore` + 轮换暴露凭证 + 移出工作区（运维工具可放 `scripts/ops/` 或本机私有目录）；不擅自删除（用户运维资产） |
| `_srv_py.py` | 同上（第二版） | ⚠️ 同上 |
| `scripts/e2e/_admin_explain.cjs` | Playwright 冒烟脚本（admin explain E2E，v22 P0-4 已从 frontend/ 迁移） | ✅ 已迁移 scripts/e2e/ |
| `landing/dev-proxy.mjs` | 本地 dev 代理（vite → 生产 HTTPS 转发） | ✅ 保留：开发工具，建议注释说明用途（已有） |

## 下一步

1. Phase 1: 写 T1/T2（版本门禁 + sync 脚本）
2. Phase 2: T3 测试隔离
3. Phase 3-4: T4/T5 前端代码落地
4. Phase 5: T6/T7 运维工具
5. Phase 7: T10 版本统一 → T11 审查 → T12 报告 → T13 沉淀

## v22.0.1 终局审计修复（2026-09-27 晚）

| 项 | 状态 | 证据 |
|---|---|---|
| config 双向校验盲区修复 | ✅ | `_config_consumed` 扩展至整个 api/；config_validate 20 passed |
| env 模板 8 变量补齐 | ✅ | .env.example + production 双向一致 |
| H1 伪测试修复 | ✅ | generator 429 真端到端（本地 FakeRequest + event: error 断言） |
| e2e_v12 注释同步 | ✅ | 20.0.0 → 22.0.0 |
| 提交推送 | ✅ | fee09cc → main + tag v22.0.1 |
| Release | ✅ | 397806294 |
| 终局 HTML 报告 | ✅ | docs/v22-terminal-report.html |
| 独立复验线程 | 🔄 | a422e6832824eab8d 运行中 |

## v22.1.1 移动端/引用核对批（2026-09-28 委派中）

| 子任务 | 角色 | 内容 | 状态 |
|---|---|---|---|
| A / P1-1 | explorer | 测试文件引用核对 + 失效修复 | 🔄 ac95b71b00119ff68 |
| B / P1-4 | worker | 375 视口移动端 Playwright 冒烟（真实 chromium） | 🔄 a4a0e03455c34859d |
| C / P1-3 | 决策 | PwaInstallPrompt 组件测试：landing 无 vitest → 由 B 的移动端冒烟覆盖 UI 行为，不引 Vue 测试栈 | ✅ 已决策 |

## v22.1.3 SSE 心跳参数化 + 真实 E2E 批（2026-09-28）

| 子任务 | 内容 | 状态 |
|---|---|---|
| A / SSE 心跳参数化 | IF_SSE_HEARTBEAT_INTERVAL 配置 + set_heartbeat_interval()/heartbeat_interval() + ws_events 动态读 + lifespan 装配 + env 模板 + 3 单测 | ✅ 已完成（sse 27 passed + config 绿） |
| B / 真实 E2E 契约 | mock_cfsolver + uvicorn + e2e_v12.py 全契约 | 🔄 acb242cebcf9f4bbe |
