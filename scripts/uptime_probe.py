"""UptimeRobot 拨测自检脚本（v21 T7）。

模拟 UptimeRobot 免费层探针行为：对 /v1/healthz 做连续 N 次探测，
验证部署前的可用性基线。上线自检用（文档 monitoring.md 与生产探针一致）。

用法:
    python scripts/uptime_probe.py [--base-url http://127.0.0.1:8100] [--times 3] [--interval 2]

退出码: 0 = 全部 200；1 = 任一次非 200/超时/网络错误。
"""

from __future__ import annotations

import argparse
import sys
import time
from urllib.request import urlopen


def probe(base_url: str, timeout: float) -> tuple[int, float]:
    url = f"{base_url}/v1/healthz"
    start = time.monotonic()
    try:
        with urlopen(url, timeout=timeout) as resp:
            latency = (time.monotonic() - start) * 1000
            return resp.status, latency
    except OSError:
        # URLError/HTTPError/连接超时均落此（URLError 是 OSError 子类）
        return -1, (time.monotonic() - start) * 1000


def main() -> int:
    ap = argparse.ArgumentParser(description="UptimeRobot 拨测自检（模拟探针）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8100", help="API 基地址")
    ap.add_argument("--times", type=int, default=3, help="连续探测次数（默认 3，对齐 UptimeRobot 5min 探针语义）")
    ap.add_argument("--interval", type=float, default=2.0, help="两次探测间隔秒数")
    ap.add_argument("--timeout", type=float, default=10.0, help="单次超时秒数")
    args = ap.parse_args()

    # Windows GBK 终端无法打印 ✅ 等非 ASCII —— 强制 UTF-8 输出避免成功路径编码崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    print(f"== uptime_probe: {args.base_url} x {args.times}（模拟 UptimeRobot 探针）==")
    ok = True
    for i in range(1, args.times + 1):
        status, latency = probe(args.base_url, args.timeout)
        if status == -1:
            print(f"  [{i}/{args.times}] {args.base_url}/v1/healthz → 网络错误/超时")
            ok = False
        elif status == 200:
            print(f"  [{i}/{args.times}] 200 OK  ({latency:.0f}ms)")
        else:
            print(f"  [{i}/{args.times}] HTTP {status}  ← 非 200，不可上线")
            ok = False
        if i < args.times:
            time.sleep(args.interval)

    if ok:
        print(f"== 连续 {args.times} 次全部 200 → 拨测通过 ✅ ==")
        return 0
    print("== 存在失败，请先修复再上线 ==")
    return 1


if __name__ == "__main__":
    sys.exit(main())