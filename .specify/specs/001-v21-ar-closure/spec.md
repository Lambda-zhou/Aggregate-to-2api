# Feature Specification: 001-v21-ar-closure

> 听风AI v21.0.0 迭代升级 —— 终局闭环 / 需求追踪矩阵 / 补漏修复。
> 对应蓝本：《计划书/下一步改进指南.md》（v21.0.0 方案，2026-09-27 实测 14+ 缺口）。

## Problem Statement（问题陈述）

生产网关（imagefree.hwhcie.bond）已具备门户/生图/聊天/Agent/画廊/可观测性等完整能力，但存在：
1. **版本号漂移**（api/pyproject=20.3.10，git HEAD=v20.3.11，e2e_v12 契约仍 20.0.0）——无自动化门禁，发版靠人肉检查易漏。
2. **测试隔离债**（test_dashboard_counts_reflect_state 存在跨实例共享状态污染）。
3. **i18n 差 1 页**（DLQ.tsx 无 i18n，15 页中唯一缺口）。
4. **PWA 不可安装**（landing 已有 sw.js+注册，缺 manifest/icon/安装提示）。
5. **litestream 秒级备份未启用**（compose backup profile 已定义，R2 凭证占位）。
6. **观察栈未启用**（compose obs profile 已定义，Grafana/Prometheus 容器未起）。
7. **运维工具缺口**：无版本同步脚本、无 uptime 自检脚本。
8. **旧产物残留**（_dep33.py/_srv_py.py 等未跟踪临时文件）。
9. **前端包体积基线未记录**、**画廊大图无懒加载**、**门户 SEO 无 sitemap/JSON-LD**。

## User Stories（用户故事）

### Story 1: 交付负责人——一次 bump 全链一致
As 交付负责人，I want 一条命令同步版本号并让 CI 自动校验，So that 发版不再出现"api 20.3.10 vs git v20.3.11"式漂移。
**Acceptance Criteria:**
- [ ] `scripts/sync_version.py --set X.Y.Z` 幂等改写全链 ~15 处并报告
- [ ] `tests/test_version_consistency.py` 在校验模式检测漂移（当前应红）并在 CI 首步 fail-fast
- [ ] e2e_v12 契约断言纳入校验

### Story 2: 开发者——测试隔离可复现
As 开发者，I want account_pool 测试组合跑稳定，So that CI 不因共享状态偶发失败。
**Acceptance Criteria:**
- [ ] 复现测试（两实例共享状态→红）存在
- [ ] 组合跑 `test_account_pool.py + fsm + predict_exhaustion` 无污染

### Story 3: 国际用户——全页面双语
As 海外用户，I want DLQ 页与其余页面同样可切换中英，So that 15/15 页 i18n 覆盖。
**Acceptance Criteria:**
- [ ] messages.zh.ts/en.ts 补 dlq.* 平行 key，isLangComplete() 断言绿
- [ ] DLQ.tsx 无中文硬编码

### Story 4: 移动用户——添加到主屏幕
As 手机用户，I want landing 可安装为应用，So that 获得原生般入口体验。
**Acceptance Criteria:**
- [ ] manifest.webmanifest + 192/512 icon + index.html link + iOS meta
- [ ] 安装提示组件（beforeinstallprompt + iOS 提示）
- [ ] 既有 sw.js 不重写、三策略保持

### Story 5: 运维——数据抗毁 + 可用性保障
As 运维，I want litestream 秒级副本可启用 + 观察栈一命令拉起 + 拨测自检，So that RPO 从 24h 降到秒级、故障可观测。
**Acceptance Criteria:**
- [ ] litestream 本地 type:local 验收命令 + 恢复 diff 脚本就绪
- [ ] compose obs profile 启动命令 + 验证命令文档化
- [ ] scripts/uptime_probe.py 连续 3 次 200
- [ ] .env.production.example 补 LITESTREAM_* 六变量

## Non-Functional Requirements（非功能）

- **兼容性**: 全部改动向后兼容，默认行为不变；新开关缺省关。
- **稳定性**: 不触碰 engine.py / adaptive_router.py 主链路；改动均带 TDD/静态验证。
- **安全**: 不新增日志泄露；SW 缓存不接 query 串；密钥仅 env 注入。
- **可验证性**: 每项有验收命令；本机可跑 pytest；前端代码落地（依赖装齐后跑 vitest，环境受限则标注）。
- **维护**: 文档/记忆/README/验收矩阵同步；复用既有轮子（compose profile/sw.js/manualChunks/_pkg_attr）。

## Success Metrics（成功度量）

- 版本门禁测试在鼠标点 CI 前先拦下漂移（当前红 → 修复后绿）。
- account_pool 组合跑 0 污染。
- i18n 15/15 页；DLQ 中文硬编码 0。
- landing dist 含 manifest + sw.js 存在且三策略保留。
- .env.production.example 含 LITESTREAM_* 六变量；SOP/架构演进文档声明 litestream 启用路径。
- 旧产物清理：无未跟踪 `_*.py` 临时文件残留（除说明保留项）。

## Out of Scope（不在本期）

- 真实 R2 上传（L2 需授权 + 真凭证）——只做到"凭证位齐全 + 本地验收命令 + 文档"。
- 真实 Grafana Cloud remote write / UptimeRobot 账户注册（外部资源）——文档化配置路径。
- 前端 vitest 实跑（node_modules 缺失，环境装好依赖后按文档补跑）。
- cf_solver 第二节点上线（需真实 solver 双进程实测）——compose 注释已备，标注待验证。
- 配置超线大文件拆分（P2 长债，本期仅审计不动刀，避免回归）。