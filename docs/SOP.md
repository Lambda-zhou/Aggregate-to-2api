# imagefree API 标准操作程序（SOP）

> 版本: 3.2.0 | 最后更新: 2026-09-26
> 适用: 本机（Windows 开发）+ 线上服务器（Azure 20.204.27.154，systemd + nginx）
> 公网入口: `https://imagefree.hwhcie.bond`（nginx 443 → FastAPI 8100）
> 当前代码版本: v20.3.x（发版前先核对 pyproject.toml/README 版本徽章）

---

## 1. 生产架构（真实现状）

| 组件 | 说明 |
|------|------|
| API 服务 | `systemd imagefree-api.service`，WorkingDirectory=/opt/imagefree-api，uvicorn :8100 |
| CF 求解 | `systemd imagefree-cfsolver.service`（camoufox，:8001） |
| Web 入口 | nginx 443（sites-available/imagefree）→ 代理 :8100 |
| 域名 | `*.hwhcie.bond` → 20.204.27.154（泛解析） |
| 数据库 | `/opt/imagefree-api/data/imagefree.db`（SQLite，WAL） |
| 前端 | landing（Vue3 门户，挂载 /）+ frontend（React 管理台，挂载 /admin） |
| 静态缓存 | nginx /assets immutable 1y + gzip（v20.3.2 起） |

**服务管理**：
```bash
systemctl status imagefree-api        # 状态
systemctl restart imagefree-api       # 重启（改代码/配置后）
journalctl -u imagefree-api -n 100    # 看日志
systemctl reload nginx                # nginx 配置热重载
```

---

## 2. 部署流程（发版）

### 2.1 本地构建（Windows）
```powershell
# 后端（无构建，git 直接部署）
cd C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\imagefree-2ai
git add -A && git commit -m "..." && git push origin main

# 前端 dist（服务器无 node，需本地构建后上传）
cd landing && npm.cmd run build       # → landing/dist
cd frontend && npm.cmd run build      # → frontend/dist（/admin）
```

### 2.2 服务器部署
```bash
# 1. 拉代码
cd /opt/imagefree-api && git pull --ff-only origin main

# 2. 上传 dist（SFTP）
#    landing/dist → /opt/imagefree-api/landing/dist（先备份 dist.bak-<版本>）
#    frontend/dist → /opt/imagefree-api/frontend/dist

# 3. 重启
systemctl restart imagefree-api && systemctl is-active imagefree-api

# 4. 验证
curl -s http://127.0.0.1:8100/v1/healthz | head -c 200
curl -s -o /dev/null -w "home=%{http_code} admin=%{http_code}\n" http://127.0.0.1:8100/ http://127.0.0.1:8100/admin/
```

### 2.3 nginx 配置改动
```bash
cp /etc/nginx/sites-available/imagefree /etc/nginx/sites-available/imagefree.bak-<日期>
# 编辑后:
nginx -t && systemctl reload nginx
```

---

## 3. 数据库备份（可恢复性保障，v20.3.5 确认配置）

**机制**：`scripts/backup_db.py`（VACUUM INTO 在线热备，WAL 安全不锁写）
- 备份范围：data/ 下全部 7 个 SQLite DB（imagefree/account_pool/dag_runs/email_registry/human_inbox/queue/skills）
- 输出：`/opt/imagefree-api/backups/<db>-<时间戳>.db`
- 校验：备份后自动 `PRAGMA integrity_check` + `requests` 行数核对
- 保留：`--keep-days 7`（超期自动清理）

**cron 调度**（已在生产配置）：
```bash
0 3 * * * cd /opt/imagefree-api && /opt/imagefree-api/.venv/bin/python scripts/backup_db.py --all --out-dir /opt/imagefree-api/backups --keep-days 7 >> /opt/imagefree-api/backups/backup.log 2>&1
```

**手动备份 / 恢复演练**：
```bash
# 手动全量备份
cd /opt/imagefree-api && .venv/bin/python scripts/backup_db.py --all --out-dir /opt/imagefree-api/backups --keep-days 7

# 恢复（用备份文件替换 data/ 下对应 db，先停服务避免写冲突）
systemctl stop imagefree-api
cp backups/imagefree-<时间戳>.db data/imagefree.db
systemctl start imagefree-api
```

**自动恢复演练**（v20.3.6 起，非破坏性：临时目录验证可恢复）：
```bash
# cron 每周日 04:00（已在生产配置）
0 4 * * 0 cd /opt/imagefree-api && /opt/imagefree-api/.venv/bin/python scripts/restore_drill.py --backup-dir /opt/imagefree-api/backups --dbs imagefree,dag_runs,queue >> /opt/imagefree-api/backups/drill.log 2>&1
# 手动演练
/opt/imagefree-api/.venv/bin/python scripts/restore_drill.py --backup-dir /opt/imagefree-api/backups --dbs imagefree
```

**异地备份（前置条件未满足，需 R2/S3 凭证后启用）**：
- 当前备份仅服务器本地（/opt/imagefree-api/backups），机器故障会丢失
- 前置：提供 R2/S3 凭证后，用 litestream 或 rclone 把 backups/ 推送到异地对象存储（RPO 秒级）
- 凭证就绪前保持本地 cron 每日备份（RPO=24h 兜底）

**litestream 秒级异地副本启用（v21.0.0 规划 / v23 systemd 原生落地，RPO 24h→秒级）**：
- **systemd 原生路径（生产 20.204.27.154，无 Docker，v23 推荐）**：配置 `deploy/systemd/litestream.yml.prod`（三库 sync-interval=1s、retention=72h，路径 /opt/imagefree-api/data/）+ unit `deploy/systemd/litestream.service`（EnvironmentFile=/opt/imagefree-api/.env 注入凭证）。启用三步：
  1. `deploy/.env` 填 `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` / `LITESTREAM_S3_BUCKET`（R2 的 S3 兼容凭证；模板见 `deploy/.env.production.example`）
  2. `cp deploy/systemd/litestream.yml.prod deploy/litestream.yml && cp deploy/systemd/litestream.service /etc/systemd/system/imagefree-litestream.service && systemctl daemon-reload && systemctl enable --now imagefree-litestream`
  3. 验证：`journalctl -u imagefree-litestream | grep -i replicating`；`litestream generations -config deploy/litestream.yml`
- **compose 路径（仅 Docker 环境）**：`cd deploy && docker compose --profile backup up -d` 等（见 git 历史 v21 段，不适用生产）
- 本地验收（无外网凭证也可验证机制）：临时 `deploy/litestream.local.yml`（`type: local` 副本）→ `litestream replicate -config deploy/litestream.local.yml` → `litestream restore -config deploy/litestream.local.yml -o restore-test/` diff 行数一致 → 验收后删除临时文件
- 恢复演练：`scripts/restore_drill.py --from-litestream`（v21 扩展，用副本还原到临时目录 diff）
- 恢复操作序：见 `deploy/docs/litestream-restore.md`

---

## 4. 功能开关（systemd 环境变量）

| 开关 | 值 | 作用 |
|------|-----|------|
| IF_PPT_GENERATE | 1 | PPT 生成（python-pptx） |
| IF_VIDEO_ENABLED | 0 | 视频 Mock（上游待接入） |
| IF_FREE_PROXY | 1 | 免费代理池（高并发换 IP） |
| IF_MOCK_UPSTREAM | (缺省) | 0=真实上游，1=Mock |
| IF_API_KEYS | 空 | 匿名开放（配 Key 则启用鉴权） |

改开关：编辑 `/etc/systemd/system/imagefree-api.service` → `systemctl daemon-reload && systemctl restart imagefree-api`

---

## 5. 常见排障

| 症状 | 排查 | 修复 |
|------|------|------|
| 图生图 500 TLS | journalctl 查 SSLV3 | 已修复（R2 SNI）v20.3.1 |
| 页面旧 chunk 404 | index.html 缓存 | nginx no-cache 已配 /assets immutable |
| 上游瞬时 429 | 重试即成功 | 代理池自动换 IP |
| cf_solver 熔断 | healthz solver_status | 重启 cfsolver 服务 |

---

## 6. 验证记录约定（防重复优化）

已闭环项记录在 `docs/verification-log.md` + workflow_status「已验证勿重做」——
新任务先查该清单，避免重复跑同一测试/优化。

## 观察栈与 solver 双节点启用（v22 P0-2/P0-3，v23 修正为 systemd 原生路径）

> **v23 关键修正**：生产 `20.204.27.154` **无 Docker**（systemd 直跑 uvicorn + nginx）。
> v22 指南的 `docker compose` 路径**不适用**，一律用 `deploy/systemd/` 原生资产。

### 观察栈（Grafana + Prometheus + node_exporter，systemd 原生）
```bash
# 一键装载（deploy/systemd/install_observability.sh：下载二进制 + chown 目录 + 装三 unit + 启服）
cd /opt/imagefree-api && bash deploy/systemd/install_observability.sh
# 等价手动路径（三服务独立 unit，v23 P1-2 修正：不得再合并单文件）：
#   systemctl daemon-reload && systemctl enable --now imagefree-prometheus imagefree-node-exporter imagefree-grafana
# 验证：prometheus 抓取 api 指标成功
curl -s "http://127.0.0.1:9090/api/v1/query" --data-urlencode 'query=up{job="imagefree-api"}' | head -c 300
# 浏览器 http://<host>:3000（admin / .env GF_ADMIN_PASSWORD）→ imagefree-overview + slo-budget 面板应有数据
# 真实指标名前缀 imagefree_*（勿臆造 http_request_duration_seconds）
# 配置：deploy/systemd/prometheus.yml.prod（target 127.0.0.1:8100 同机）；grafana provisioning 在 deploy/grafana/
```

### litestream 秒级异地复制（systemd 原生，替代 compose backup profile）
```bash
# 1. R2 凭证填 /opt/imagefree-api/.env（LITESTREAM_ACCESS_KEY_ID / SECRET_ACCESS_KEY / S3_BUCKET）
# 2. 用生产配置替换（litestream.yml.prod 已指向 /opt/imagefree-api/data/，与真实库对齐）：
cp deploy/systemd/litestream.yml.prod deploy/litestream.yml
# 3. 装载 unit 并启用
cp deploy/systemd/litestream.service /etc/systemd/system/imagefree-litestream.service
systemctl daemon-reload && systemctl enable --now imagefree-litestream
# 4. 验证
journalctl -u imagefree-litestream -n 30 | grep -i "replicating"
# 本地无 R2 凭证时可先 type:local 验收机制：临时 litestream.local.yml（type: local）→
#   litestream replicate -config litestream.local.yml → litestream restore -config litestream.local.yml -o restore-test/ diff
```

### cf_solver 第二节点（真瓶颈缓解，systemd 原生多实例）
```bash
# 方案：复制 imagefree-cfsolver.service 为 imagefree-cfsolver2.service（改 WorkingDirectory 不变、
#   Environment 里端口 --port 9001），再加另一实例监听 :8002：
cp /etc/systemd/system/imagefree-cfsolver.service /etc/systemd/system/imagefree-cfsolver2.service
#   编辑 cfsolver2.service：ExecStart 追加 --port 8002（或 fallback 到配置端口）
# 再改 api 的 Environment（/etc/systemd/system/imagefree-api.service 内）：
#   IF_CF_SOLVER_URLS=http://127.0.0.1:8001,http://127.0.0.1:8002
#   IF_SOLVER_NODE_WEIGHTS=1,1   # 按需加权
systemctl daemon-reload && systemctl restart imagefree-cfsolver imagefree-cfsolver2 imagefree-api
# solver_guard 已支持多节点加权最少在途调度 + 熔断 failover（无需改 api/ 源码）
# 验收：py scripts/probe_federation.py（v23 新增，mock 双节点真实分流数字对照）
```
