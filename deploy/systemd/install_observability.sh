#!/usr/bin/env bash
# imagefree — systemd 原生观察栈一键装载（生产 20.204.27.154，Azure，无 Docker）
# v23 P1-2（独立审查修正）：原单文件塞 3 组 unit 只认最后一组 → 现拆 prometheus/node_exporter/grafana
# 三个独立 unit 文件，本脚本依次装载三服务。grafana OSS tar 二进制为 bin/grafana-server（非 grafana）。
# 用法：bash install_observability.sh
# 作用：下载二进制 → chown 数据目录 → 装载 3 个 unit → 拷贝生产配置 → 启动
# 注意：本脚本仅在 Linux 服务器执行（本地 Windows 无 systemctl）。

set -euo pipefail

GH="https://github.com/prometheus/prometheus/releases/download/v2.53.0/prometheus-2.53.0.linux-amd64.tar.gz"
GH_NODE="https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz"
GF="https://dl.grafana.com/oss/release/grafana-11.1.4.linux-amd64.tar.gz"
BIN_DIR="/usr/local/bin"
DEPLOY_DIR="/opt/imagefree-api/deploy"
DATA_DIR="/var/lib"
LOG_DIR="/var/log/imagefree-grafana"
SVC_DIR="/etc/systemd/system"

echo "==> 0. 前置检查"
command -v curl >/dev/null || { echo "缺少 curl"; exit 1; }
command -v tar  >/dev/null || { echo "缺少 tar";  exit 1; }
[ -f "$DEPLOY_DIR/systemd/prometheus.service" ] || { echo "缺少 deploy/systemd/prometheus.service"; exit 1; }

echo "==> 1. 下载二进制（若不存在）"
[ -x "$BIN_DIR/prometheus" ] || { curl -sL "$GH" -o /tmp/prom.tar.gz && tar -xzf /tmp/prom.tar.gz -C /tmp && cp /tmp/prometheus-*/prometheus "$BIN_DIR/prometheus"; }
[ -x "$BIN_DIR/node_exporter" ] || { curl -sL "$GH_NODE" -o /tmp/ne.tar.gz && tar -xzf /tmp/ne.tar.gz -C /tmp && cp /tmp/node_exporter-*/node_exporter "$BIN_DIR/node_exporter"; }
# Grafana OSS tar 二进制名为 bin/grafana-server（v23 P1-2 修正，勿用 bin/grafana）
[ -x "$BIN_DIR/grafana-server" ] || { curl -sL "$GF" -o /tmp/gf.tar.gz && tar -xzf /tmp/gf.tar.gz -C /tmp && mkdir -p "$DEPLOY_DIR/grafana/plugins" && cp /tmp/grafana-*/bin/grafana-server "$BIN_DIR/grafana-server"; }

echo "==> 2. 建数据目录并 chown 到运行用户（v23 P2-1：服务 User=ubuntu，目录须属 ubuntu）"
mkdir -p "$DATA_DIR/imagefree-prometheus" "$DATA_DIR/imagefree-grafana" "$LOG_DIR"
chown -R ubuntu:ubuntu "$DATA_DIR/imagefree-prometheus" "$DATA_DIR/imagefree-grafana" "$LOG_DIR"

echo "==> 3. 装载 3 个 systemd unit"
cp "$DEPLOY_DIR/systemd/prometheus.service" "$SVC_DIR/imagefree-prometheus.service"
cp "$DEPLOY_DIR/systemd/node_exporter.service" "$SVC_DIR/imagefree-node-exporter.service"
cp "$DEPLOY_DIR/systemd/grafana.service" "$SVC_DIR/imagefree-grafana.service"

echo "==> 4. 拷贝生产监控配置"
cp "$DEPLOY_DIR/systemd/prometheus.yml.prod" "$DEPLOY_DIR/prometheus.yml"
# grafana custom.ini：仅本机监听 + 数据/日志路径（密码经 EnvironmentFile 注入 /opt/imagefree-api/.env）
mkdir -p "$DEPLOY_DIR/grafana"
cat > "$DEPLOY_DIR/grafana/custom.ini" <<'EOF'
[server]
http_addr = 127.0.0.1
http_port = 3000
root_url = http://127.0.0.1:3000
[paths]
data = /var/lib/imagefree-grafana
logs = /var/log/imagefree-grafana
EOF
# provisioning 里 prometheus datasource 需指本机 9090（compose 版默认 prometheus:9090，systemd 需覆盖）
mkdir -p "$DEPLOY_DIR/grafana/provisioning/datasources"
cat > "$DEPLOY_DIR/grafana/provisioning/datasources/prometheus.yml" <<'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://127.0.0.1:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: 15s
      httpMethod: POST
      manageAlerts: false
EOF

echo "==> 5. 启服"
systemctl daemon-reload
systemctl enable --now imagefree-prometheus imagefree-node-exporter imagefree-grafana

echo "==> 6. 验证"
curl -s "http://127.0.0.1:9090/api/v1/query" --data-urlencode 'query=up{job="imagefree-api"}' | head -c 300
echo
systemctl is-active imagefree-prometheus imagefree-node-exporter imagefree-grafana
journalctl -u imagefree-grafana -n 10 --no-pager | tail -10

echo "==> 完成。grafana 面板：http://<host>:3000（管理员密码见 /opt/imagefree-api/.env 的 GF_ADMIN_PASSWORD）"
echo "==> 提示：若需公网访问 grafana，用 nginx 反向代理 + basic auth 而非直开端口。"