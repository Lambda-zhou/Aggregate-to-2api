"""T1 交付物：读路径极限施压探针（010 US2 落地）。

并发阶梯压测只读端点（/v1/healthz /v1/gallery /v1/stats /v1/providers /v1/models），
逐档记录 p50/p95/p99 延迟、致命错误率、进程存活，输出 JSON + 控制台摘要。

设计对齐既有 `scripts/probe_federation.py` 先例（asyncio.gather + Semaphore 控制并发），
最小依赖（仅 httpx），符合宪法「只追加不造轮子」。

用法（仓库根，Windows 用带依赖解释器）：
  .venv/Scripts/python.exe scripts/probe_stress.py [--url http://127.0.0.1:8100] \\
      [--levels 1,10,50,100,200] [--per-level 30] [--timeout 10] [--out docs/research/stress-v23.json]

崩溃判定（v23.1 T6-F1 统一口径）：任一档**致命错误率（5xx + 客户端异常，429 不计）**
>5% 即记录崩溃阈值并提前终止；429 属限流/队列满的优雅降级（Retry-After），不判崩溃。
结束时 GET /healthz 一次作为进程存活断言（T6-F4）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_ENDPOINTS = ["/v1/healthz", "/v1/gallery?limit=10", "/v1/stats", "/v1/providers", "/v1/models"]


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


async def _probe_level(
    client: httpx.AsyncClient,
    url: str,
    endpoint: str,
    concurrency: int,
    total: int,
    timeout: float,
) -> dict:
    """单档压测：Semaphore 限并发，发 total 个请求。返回分布统计。"""
    sem = asyncio.Semaphore(concurrency)
    durations: list[float] = []
    status_counts: dict[int, int] = {}
    errors: list[str] = []

    async def _one() -> None:
        async with sem:
            t0 = time.monotonic()
            try:
                r = await client.get(f"{url}{endpoint}", timeout=timeout)
                status_counts[r.status_code] = status_counts.get(r.status_code, 0) + 1
            except Exception as e:  # noqa: BLE001  # 超时/连接拒绝归类
                errors.append(type(e).__name__)
            finally:
                durations.append(time.monotonic() - t0)

    await asyncio.gather(*[_one() for _ in range(total)])
    total_ok = sum(c for s, c in status_counts.items() if 200 <= s < 300)
    total_429 = status_counts.get(429, 0)
    total_5xx = sum(c for s, c in status_counts.items() if s >= 500)
    # v23.1 T6-F1（独立审查）：崩溃判定必须**排除 429**——429 是限流/队列满的**优雅降级**
    # （Retry-After 语义），不是服务崩溃。若把 429 计入 error_rate，设了低限流后压测会把
    # "限流保护生效"误报为"服务崩溃"。错误率 = (5xx + 客户端异常) / total，429 单独列示。
    fatal = total_5xx + len(errors)
    return {
        "endpoint": endpoint,
        "concurrency": concurrency,
        "requests": total,
        "ok": total_ok,
        "status_429": total_429,
        "status_5xx": total_5xx,
        "client_errors": len(errors),
        "error_rate": round(fatal / total, 4),  # 5xx+客户端异常（429 不计入崩溃判定）
        "fatal_count": fatal,
        "p50_ms": round(_pct(durations, 50) * 1000, 1),
        "p95_ms": round(_pct(durations, 95) * 1000, 1),
        "p99_ms": round(_pct(durations, 99) * 1000, 1),
        "max_ms": round(max(durations, default=0) * 1000, 1),
    }


async def _main(args: argparse.Namespace) -> int:
    levels = [int(x) for x in args.levels.split(",")]
    url = args.url.rstrip("/")
    results: list[dict] = []
    crashed = False

    async with httpx.AsyncClient(timeout=args.timeout, limits=httpx.Limits(max_connections=512)) as client:
        for level in levels:
            print(f"\n==> 并发 {level}（每档 {args.per_level} 请求 × {len(DEFAULT_ENDPOINTS)} 端点）")
            for ep in DEFAULT_ENDPOINTS:
                r = await _probe_level(client, url, ep, level, args.per_level, args.timeout)
                results.append(r)
                flag = "  ⚠" if r["status_5xx"] or r["client_errors"] else "  ✓"
                print(
                    f"{flag} {ep:<28} ok={r['ok']}/{r['requests']} 429={r['status_429']} "
                    f"5xx={r['status_5xx']} p50={r['p50_ms']}ms p95={r['p95_ms']}ms p99={r['p99_ms']}ms"
                )
                # v23.1 T6-F1：崩溃判定用 fatal（5xx+客户端异常），429 为优雅降级不判崩溃
                if r["error_rate"] > 0.05:
                    print(f"  !! 并发 {level} 端点 {ep} 致命错误率 {r['error_rate']:.1%} "
                          f"(5xx={r['status_5xx']} client={r['client_errors']}) —— 已达崩溃阈值，提前终止")
                    crashed = True
                    break
            if crashed:
                break

    # v23.1 T6-F4（独立审查）：结束前 GET /healthz 作为进程存活断言（补 plan 声明 poll 未落地缺口）
    alive = "unknown"
    try:
        h = await httpx.AsyncClient(timeout=5.0).get(f"{url}/healthz")
        alive = "alive" if h.status_code < 500 else f"degraded-{h.status_code}"
    except Exception:
        alive = "unreachable"

    payload = {
        "url": url, "levels": levels, "per_level": args.per_level,
        "results": results, "crashed_at": None, "process_alive": alive,
    }
    if crashed:
        payload["crashed_at"] = level
    print(f"==> 进程存活: {alive}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==> 结果写入 {out}")
    return 0 if not crashed else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="读路径极限施压探针（010 US2）")
    ap.add_argument("--url", default="http://127.0.0.1:8100")
    ap.add_argument("--levels", default="1,10,50,100,200")
    ap.add_argument("--per-level", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--out", default="docs/research/stress-v23.json")
    args = ap.parse_args()
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
