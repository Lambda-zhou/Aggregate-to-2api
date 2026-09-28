"""N5 交付物：cf_solver 双节点联邦真实分流数字验证探针。

真实起 2 个 mock cf_solver 节点（默认 127.0.0.1:8001 / 8002，node-id node-1/node-2，
delay 0.05 / 0.15 便于区分），通过 api.solver_guard.SolverGuard 的联邦调度
（select_node 最少在途轮询 / select_candidates 生产主路径 + acquire/release + record_*）
并发求解，统计每个节点实际承接数（以 mock 返回的 node_id 为准）与均时，
输出"节点承接分布 + 均时"，并检查是否有节点熔断。

场景：
  0/1. 单节点对照（node-1 delay=0.05 / node-2 delay=0.15）→ 均时基准
  2.   双节点联邦 select_node（最少在途联邦轮询）N 并发 → 承接分布 + 均时
  3.   双节点联邦 select_candidates[0]（turnstile_client 生产主路径）N 并发
  4.   双节点联邦持续压测（并发 C、总量 T）→ 展示 least-inflight 偏向快节点

用法（仓库根目录，须用带依赖的 venv 解释器）：
  .venv/Scripts/python.exe scripts/probe_federation.py [--n 20] [--concurrent 20]
  .venv/Scripts/python.exe scripts/probe_federation.py --only basic   # 跳过场景4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
MOCK_SCRIPT = ROOT / "scripts" / "mock_cfsolver.py"
SITEKEY = "0x4AAAAAACE-XLGoQUckKKm_"
TARGET_URL = "http://example.com/page"

_mocks: list[subprocess.Popen] = []


def _start_mock(port: int, node_id: str, delay: float) -> subprocess.Popen:
    """后台起一个 mock cf_solver 节点（Windows 兼容：同解释器子进程）。"""
    proc = subprocess.Popen(
        [
            sys.executable,
            str(MOCK_SCRIPT),
            "--port",
            str(port),
            "--node-id",
            node_id,
            "--delay",
            str(delay),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _mocks.append(proc)
    return proc


async def _wait_ready(client: httpx.AsyncClient, base: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"{base}/__status", timeout=1.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError(f"mock 节点 {base} 未就绪")


async def _solve_node(
    client: httpx.AsyncClient,
    base: str,
    guard,
    timeout: float = 15.0,
    poll_interval: float = 0.05,
) -> dict:
    """模拟 turnstile_client 对单节点的求解：/turnstile → 轮询 /result → 上报 guard。

    返回 {"ok", "node", "total", "solve_window", "queue"}；node 取自 mock 响应体的
    node_id（实际承接节点）；total=请求→终态，solve_window=202→200（≈真实求解），
    queue=请求→202（本机 HTTP 排队开销）。
    """
    t0 = time.monotonic()
    node_state = guard.acquire_inflight_for(base)
    try:
        resp = await client.get(
            f"{base}/turnstile", params={"url": TARGET_URL, "sitekey": SITEKEY}, timeout=timeout
        )
        t202 = time.monotonic()
        if resp.status_code != 202:
            duration = time.monotonic() - t0
            guard.record_failure("http_error", duration, node_url=base)
            return {"ok": False, "node": _node_of(base), "total": duration,
                    "solve_window": 0.0, "queue": duration}
        task_id = resp.json()["task_id"]
        while True:
            res = await client.get(f"{base}/result", params={"id": task_id}, timeout=timeout)
            if res.status_code == 200:
                body = res.json()
                t200 = time.monotonic()
                guard.record_success(t200 - t0, node_url=base)
                return {
                    "ok": True,
                    "node": body.get("node_id") or _node_of(base),
                    "total": t200 - t0,
                    "solve_window": t200 - t202,
                    "queue": t202 - t0,
                }
            if res.status_code in (408, 422):
                duration = time.monotonic() - t0
                guard.record_failure("solver_rejected", duration, node_url=base)
                return {"ok": False, "node": _node_of(base), "total": duration,
                        "solve_window": 0.0, "queue": t202 - t0}
            if res.status_code not in (200, 202):
                duration = time.monotonic() - t0
                guard.record_failure("http_error", duration, node_url=base)
                return {"ok": False, "node": _node_of(base), "total": duration,
                        "solve_window": 0.0, "queue": t202 - t0}
            await asyncio.sleep(poll_interval)
    finally:
        if node_state:
            guard.release_inflight_for(base)


_NODE_BY_PORT: dict[str, str] = {}


def _node_of(url: str) -> str:
    """由 URL 端口反查节点名（仅当响应体缺 node_id 时兜底，正常路径用 mock 返回的 node_id）。"""
    port = url.rsplit(":", 1)[-1]
    return _NODE_BY_PORT.get(port, f"node-{port}")


async def _run_scenario(
    client: httpx.AsyncClient, guard, total: int, concurrent: int, mode: str, poll_interval: float = 0.05
) -> list[dict]:
    sem = asyncio.Semaphore(concurrent)

    async def _one() -> dict:
        async with sem:
            if mode == "select_node":
                node = guard.select_node()
            else:  # select_candidates[0] 生产主路径
                cands = guard.select_candidates()
                node = cands[0] if cands else None
            if node is None:
                return {"ok": False, "node": None, "total": 0.0, "solve_window": 0.0, "queue": 0.0}
            return await _solve_node(client, node.url, guard, poll_interval=poll_interval)

    return await asyncio.gather(*[_one() for _ in range(total)])


def _summarize(label: str, results: list[dict], guard) -> dict:
    by_node: dict[str, dict] = {}
    for r in results:
        node = r["node"]
        if r["ok"]:
            entry = by_node.setdefault(node, {"count": 0, "totals": [], "windows": []})
            entry["count"] += 1
            entry["totals"].append(r["total"])
            entry["windows"].append(r["solve_window"])
    counts = {n: v["count"] for n, v in by_node.items()}
    totals = [d for v in by_node.values() for d in v["totals"]]
    windows = [d for v in by_node.values() for d in v["windows"]]
    snap = guard.snapshot()
    node_status = {n["url"]: {"status": n["status"], "circuit_open": n["circuit_open"],
                              "solve_success_total": n["solve_success_total"]}
                   for n in snap["nodes"]}
    summary = {
        "label": label,
        "total": len(results),
        "success": sum(1 for r in results if r["ok"]),
        "failure": sum(1 for r in results if not r["ok"]),
        "node_counts": counts,
        "avg_seconds": round(statistics.mean(totals), 4) if totals else None,
        "solve_window_p50_seconds": round(statistics.median(windows), 4) if windows else None,
        "solve_window_min_seconds": round(min(windows), 4) if windows else None,
        "min_seconds": round(min(totals), 4) if totals else None,
        "max_seconds": round(max(totals), 4) if totals else None,
        "node_status": node_status,
    }
    _print_summary(summary)
    return summary


def _print_summary(s: dict) -> None:
    print(f"  [{s['label']}] total={s['total']} ok={s['success']} fail={s['failure']}")
    print(f"    承接分布: {s['node_counts']}")
    if s["avg_seconds"] is not None:
        print(
            f"    均时(total): {s['avg_seconds']:.4f}s (min={s['min_seconds']:.4f} max={s['max_seconds']:.4f})"
        )
        print(
            f"    求解窗口(202→200): p50={s['solve_window_p50_seconds']:.4f}s min={s['solve_window_min_seconds']:.4f}s"
        )
    for url, st in s["node_status"].items():
        print(f"    节点 {url}: status={st['status']} circuit_open={st['circuit_open']} "
              f"success_total={st['solve_success_total']}")


async def _main(args: argparse.Namespace) -> None:
    n1_url, n2_url = f"http://127.0.0.1:{args.node1_port}", f"http://127.0.0.1:{args.node2_port}"
    _NODE_BY_PORT[str(args.node1_port)] = "node-1"
    _NODE_BY_PORT[str(args.node2_port)] = "node-2"

    # 起 2 个 mock 节点：node-1 delay 0.05（快）/ node-2 delay 0.15（慢）
    _start_mock(args.node1_port, "node-1", 0.05)
    _start_mock(args.node2_port, "node-2", 0.15)

    from api.solver_guard import SolverGuard  # 延迟导入，避免未起节点时 import 失败

    async with httpx.AsyncClient() as client:
        await _wait_ready(client, n1_url)
        await _wait_ready(client, n2_url)
        print(f"[probe] 双 mock 节点就绪: {n1_url} (node-1, delay 0.05s), {n2_url} (node-2, delay 0.15s)")

        summaries = []

        # ── 场景 0/1：单节点对照基准（串行并发度=1，排除本机 HTTP 排队污染）──
        for port, node_id, delay in ((args.node1_port, "node-1", 0.05), (args.node2_port, "node-2", 0.15)):
            url = f"http://127.0.0.1:{port}"
            guard = SolverGuard(urls=[url])
            results = await _run_scenario(client, guard, args.n, 1, "candidates", args.poll)
            summaries.append(_summarize(f"单节点 {node_id} (delay {delay}s, 串行)", results, guard))

        # ── 场景 2：双节点联邦 select_node（最少在途轮询，并发 N）──
        guard = SolverGuard(urls=[n1_url, n2_url])
        results = await _run_scenario(client, guard, args.n, args.n, "select_node", args.poll)
        summaries.append(_summarize("双节点联邦 select_node (并发)", results, guard))

        # ── 场景 3：双节点联邦 select_candidates[0]（生产主路径，并发 N）──
        guard = SolverGuard(urls=[n1_url, n2_url])
        results = await _run_scenario(client, guard, args.n, args.n, "candidates", args.poll)
        summaries.append(_summarize("双节点联邦 select_candidates[0] (并发)", results, guard))

        # ── 场景 4：持续压测（least-inflight 偏向快节点）──
        if not args.only_basic:
            guard = SolverGuard(urls=[n1_url, n2_url])
            results = await _run_scenario(client, guard, args.total, args.concurrent, "select_node", args.poll)
            summaries.append(_summarize("双节点联邦 持续压测(least-inflight)", results, guard))

        payload = {
            "scenario": "cf_solver 双节点联邦真实分流验证 (mock)",
            "nodes": [{"url": n1_url, "node_id": "node-1", "delay": 0.05},
                      {"url": n2_url, "node_id": "node-2", "delay": 0.15}],
            "summaries": summaries,
        }
        out = Path(args.out)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[probe] JSON 结果已写入 {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="cf_solver 双节点联邦真实分流数字验证探针")
    ap.add_argument("--n", type=int, default=20, help="单场景求解总数 (default 20)")
    ap.add_argument("--concurrent", type=int, default=10, help="场景4 并发度 (default 10)")
    ap.add_argument("--total", type=int, default=40, help="场景4 总请求数 (default 40)")
    ap.add_argument("--poll", type=float, default=0.05, help="/result 轮询间隔秒 (default 0.05)")
    ap.add_argument("--node1-port", type=int, default=8001)
    ap.add_argument("--node2-port", type=int, default=8002)
    ap.add_argument("--only-basic", action="store_true", help="只跑场景 0-3，跳过持续压测")
    ap.add_argument("--out", default=str(ROOT / "docs/research/solver-federation-v23.json"),
                    help="JSON 结果输出路径")
    args = ap.parse_args()
    try:
        asyncio.run(_main(args))
    finally:
        for proc in _mocks:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
