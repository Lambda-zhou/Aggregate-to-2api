"""scripts/uptime_probe.py 单测（v22 T3）。

用标准库 http.server 起本地探针端点，在随机端口验证 200/503/超时/非 ASCII 输出。
完全不触碰外部网络与真实服务。
"""

from __future__ import annotations

import functools
import http.server
import importlib.util
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uptime_probe.py"


def _load_probe() -> "ModuleType":
    spec = importlib.util.spec_from_file_location("uptime_probe_mod", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Handler(http.server.BaseHTTPRequestHandler):
    """可配状态码的探针 handler。"""

    status = 200

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}' if self.status == 200 else b'{"status":"degraded"}')

    def log_message(self, *args: object) -> None:  # 静默
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def local_server():
    """起随机端口探针端点，yield base_url，teardown 关闭。"""
    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def local_server_503(local_server: str):
    """把 handler 状态改为 503，复用同一 server。"""
    _Handler.status = 503
    yield local_server
    _Handler.status = 200


def test_probe_function_200(local_server: str) -> None:
    """probe() 对 200 端点返回 (200, latency>=0)。"""
    mod = _load_probe()
    status, latency = mod.probe(local_server, timeout=5.0)
    assert status == 200
    assert latency >= 0


def test_probe_function_503(local_server_503: str) -> None:
    """probe() 对 503 返回 HCStatus 503（非 -1，超时/连接失败才是 -1）。"""
    mod = _load_probe()
    status, _ = mod.probe(local_server_503, timeout=5.0)
    assert status == 503


def test_probe_function_unreachable() -> None:
    """不可达端口返回 -1（网络错误路径）。"""
    unavail = f"http://127.0.0.1:{_free_port()}"
    mod = _load_probe()
    status, _ = mod.probe(unavail, timeout=2.0)
    assert status == -1


def test_cli_all_200_exit0(local_server: str) -> None:
    """CLI 连续 3 次全 200 → exit 0。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-url", local_server, "--times", "3", "--interval", "0"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"


def test_cli_503_exit1(local_server_503: str) -> None:
    """CLI 遇 503 → exit 1。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-url", local_server_503, "--times", "1", "--interval", "0"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert r.returncode == 1


def test_cli_gbk_success_no_crash(local_server: str) -> None:
    """GBK 终端下成功路径含 ✅/非 ASCII 不崩溃（B2 修复回归）。"""
    env = {**__import__("os").environ, "PYTHONIOENCODING": "cp936"}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--base-url", local_server, "--times", "1", "--interval", "0"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert r.returncode == 0, f"GBK 成功路径不可崩: rc={r.returncode} stderr={r.stderr}"