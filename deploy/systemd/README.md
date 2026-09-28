# systemd 原生部署资产（生产：20.204.27.154，Azure）

> **适用事实**：线上服务器**无 Docker**（systemd 直跑 uvicorn + nginx 443 → :8100）。
> v22 指南中所有 `docker compose` 路径（litestream / prometheus / grafana / cf_solver 双节点）
> 在此**不适用**，一律使用本目录的原生 unit 模板。
> 目录中的 `.service` 为**待生产 apply 模板**——已静态审查 + 语法校验，真实装载需在
> 服务器执行 `systemctl daemon-reload`（本地 Windows 无 systemctl，标注「待生产 apply」）。

## 资产清单

| 文件 | 用途 | 状态 |
|---|---|---|
| `prometheus.service` | 指标抓取（同机 :8100/metrics）— **独立 unit**（v23 P1-2） | 模板就绪，需下载二进制 + apply |
| `node_exporter.service` | 宿主机系统指标（cpu/mem/disk/net）— **独立 unit**（v23 P1-2） | 模板就绪，需下载二进制 + apply |
| `grafana.service` | 面板（imagefree-overview + slo-budget）— **独立 unit**（v23 P1-2） | 模板就绪，需下载二进制 + apply |
| `litestream.service` | 秒级 WAL 异地复制（R2/S3，三库） | 模板就绪，需 R2 凭证 + apply |
| `litestream.yml.prod` | 生产版 litestream 配置（路径 /opt/imagefree-api/data/） | 模板就绪 |
| `prometheus.yml.prod` | 生产版抓取配置（target 127.0.0.1:8100） | 模板就绪 |
| `install_observability.sh` | 一键下载二进制 + chown 目录 + 装载三 unit + 启服 | 脚本就绪（bash，仅服务器用） |

> ⚠ v23 P1-2 修正：**不再有合并的 `observability.service`**（单文件塞 3 组 unit，systemd 只认
> 最后一组 ExecStart → prometheus/node_exporter 永不启动）。观察栈 = 三个独立 unit
> `imagefree-prometheus` / `imagefree-node-exporter` / `imagefree-grafana`。
> Grafana 二进制为 `/usr/local/bin/grafana-server`（OSS tar 内是 bin/grafana-server，非 grafana）。

## 各服务启用顺序（生产 apply 后）

```bash
# 1. litestream（RPO 24h→秒级；需先在 /opt/imagefree-api/.env 填 R2 凭证，
#    unit 已 EnvironmentFile=/opt/imagefree-api/.env）
systemctl daemon-reload && systemctl enable --now imagefree-litestream
journalctl -u imagefree-litestream -n 30   # 应见 "replicating"

# 2. 观察栈（prometheus + node_exporter + grafana 三个独立 unit）
systemctl daemon-reload && systemctl enable --now imagefree-prometheus imagefree-node-exporter imagefree-grafana
# 验证抓取
curl -s "http://127.0.0.1:9090/api/v1/query" --data-urlencode 'query=up{job="imagefree-api"}' | head -c 200
# 浏览器 http://<host>:3000（管理员密码在 /opt/imagefree-api/.env 的 GF_ADMIN_PASSWORD；首次登录改密）
#   → imagefree-overview / slo-budget 面板有数据
```

## 关键差异（vs v22 指南 compose 路径）

| 项 | compose 版（不适用） | systemd 原生版（本目录） |
|---|---|---|
| litestream | `docker compose --profile backup up -d` | `systemctl start imagefree-litestream` |
| prometheus target | `api:8100`（容器网络名） | `127.0.0.1:8100`（同机） |
| grafana | compose obs profile | systemd unit + 数据目录 /var/lib/imagefree-grafana |
| cf_solver 双节点 | compose 复制 service 块 | 复制 `imagefree-cfsolver.service` 为 `imagefree-cfsolver2.service` + `Environment=IF_CF_SOLVER_URLS=...` 双 URL |
