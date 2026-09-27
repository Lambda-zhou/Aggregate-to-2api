# Implementation Tasks: 001-v21-ar-closure

> Spec-Kit Phase 5 ｜ 依赖排序 ｜ 每项验收标准见 plan.md。

## Phase 1: 版本门禁基座（防漂移）

- [ ] 1.1 [P] 编写 `tests/test_version_consistency.py`（全链读取比对 + semver + e2e_v12 断言；当前应为红）
  - 读取：pyproject.toml / api/main.py / frontend/package.json / landing/package.json / deploy/docker-compose.yml / README badge / desktop 三件套 / scripts/e2e_v12.py 断言
  - **Depends on**: None | **Req**: S1
- [ ] 1.2 [P] 编写 `scripts/sync_version.py`（--set/--check/--dry-run 幂等）
  - **Depends on**: 1.1 | **Req**: S1

## Phase 2: 测试隔离根治（account_pool）

- [ ] 2.1 复现测试（两实例共享状态断言 → 红）
- [ ] 2.2 定位并改为实例属主（self._*），保持 _pkg_attr 契约
  - **Depends on**: 2.1
- [ ] 2.3 组合跑 `test_account_pool + fsm + predict_exhaustion` 0 污染
  - **Depends on**: 2.2

## Phase 3: i18n 收尾（DLQ 页）

- [ ] 3.1 messages.zh.ts/en.ts 补 `dlq.*` 平行 key（~18 个）
- [ ] 3.2 DLQ.tsx 去中文硬编码换 `t('dlq.xxx')` + 空态/加载/错误态
- [ ] 3.3 静态验证：`rg "[一-龥]" src/pages/DLQ.tsx | wc -l` → 0
  - **Depends on**: 3.1, 3.2

## Phase 4: PWA installable（landing 主战场）

- [ ] 4.1 `landing/public/manifest.webmanifest`（theme_color=#0a0e1a 对齐 index.html L41-42）
- [ ] 4.2 192/512 icon（用 og-image.png 缩放或品牌色生成）+ index.html manifest link + apple-touch-icon + iOS meta
- [ ] 4.3 `landing/src/components/PwaInstallPrompt.vue`（beforeinstallprompt/iOS standalone/计数阈值/桌面隐藏）
- [ ] 4.4 build 产物含 manifest；sw.js 三策略保留（不重写）
  - **Depends on**: 4.1, 4.2, 4.3

## Phase 5: litestream 启用路径 + 运维工具（非 R2 真接）

- [ ] 5.1 `deploy/.env.production.example` 补 LITESTREAM_* 六变量
- [ ] 5.2 `docs/SOP.md` §3 追加 litestream 启用小节（本地 type:local 验收 + R2 授权路径）
- [ ] 5.3 `scripts/uptime_probe.py`（GET /v1/healthz 连跑 3 次）
- [ ] 5.4 观察栈验证命令 + 真实指标名确认（compose obs 启动与面板验证写入文档）
  - **Depends on**: None（并行）

## Phase 6: SEO + 无风险落地页增强

- [ ] 6.1 landing `public/sitemap.xml`（/ + /privacy）+ robots.txt + 首页 JSON-LD + og meta 核对
  - **Depends on**: None

## Phase 7: 收尾（版本统一 + 清理 + 归档）

- [ ] 7.1 version 全链统一 21.0.0（sync_version.py --set + --check）【若获用户许可才 bump，否则保持 20.3.11 对齐】
- [ ] 7.2 旧产物清理（`_dep33.py`/`_srv_py.py`/临时文件的处理决策：保留标注 or 移除）
- [ ] 7.3 README.md v21 小节 + verification-log 追加 + workflow_status.md 归档
  - **Depends on**: 全部

## Notes

- `[P]` = 可与兄弟任务并行
- 前端修改（3.x/4.x/6.x）本机 node_modules 缺失 → 代码落地 + 静态验证 + 在 verification-log 标"依赖装齐后跑 vitest"
- 后端修改（1.x/2.x/5.x）本机 pytest 9.1.1 可实跑
- litestream R2 / Grafana Cloud / UptimeRobot 账户 = 外部资源，文档化路径，不伪装启用