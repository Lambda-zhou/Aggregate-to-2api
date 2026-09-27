"""版本全链同步工具（v21 T2）。

用法:
    python scripts/sync_version.py --check              # 只读校验全链一致，不一致则非零退出
    python scripts/sync_version.py --dry-run            # 报告差异但不落盘
    python scripts/sync_version.py --set 21.0.0         # 幂等批量改写全链 ~15 处
    python scripts/sync_version.py --set 21.0.0 --dry-run

覆盖: pyproject.toml / deploy/pyproject.toml / api/main.py /
      frontend/package.json / landing/package.json / desktop/package.json /
      desktop/src-tauri/tauri.conf.json / desktop/src-tauri/Cargo.toml /
      README.md badge / scripts/e2e_v12.py 契约断言
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ROOT 可被 SYNC_VERSION_ROOT 环境变量覆盖（单测注入 tmp 树隔离）；默认仓库根，向后兼容
ROOT = Path(os.environ.get("SYNC_VERSION_ROOT") or Path(__file__).resolve().parents[1])
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# (相对路径, 匹配模式, 替换函数)
TARGETS: list[tuple[str, str, str]] = [
    ("pyproject.toml", r'^version = "([^"]+)"', 'version = "{v}"'),
    ("deploy/pyproject.toml", r'^version = "([^"]+)"', 'version = "{v}"'),
    ("api/main.py", r'version="([^"]+)"', 'version="{v}"'),
    ("frontend/package.json", r'"version": "([^"]+)"', '"version": "{v}"'),
    ("landing/package.json", r'"version": "([^"]+)"', '"version": "{v}"'),
    ("desktop/package.json", r'"version": "([^"]+)"', '"version": "{v}"'),
    ("desktop/src-tauri/tauri.conf.json", r'"version": "([^"]+)"', '"version": "{v}"'),
    ("desktop/src-tauri/Cargo.toml", r'^version = "([^"]+)"', 'version = "{v}"'),
    ("README.md", r"version-([0-9]+\.[0-9]+\.[0-9]+)", "version-{v}"),
]

# e2e_v12.py 契约断言版本（正则捕获整数字面量）
E2E_PATTERN = r'"(\d+\.\d+\.\d+)"'


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def current_versions() -> dict[str, str]:
    """读取各源当前版本，返回 {相对路径: 版本}。"""
    out: dict[str, str] = {}
    for rel, pattern, _ in TARGETS:
        p = ROOT / rel
        if not p.exists():
            out[rel] = "<MISSING>"
            continue
        m = re.search(pattern, read(p), flags=re.MULTILINE)
        out[rel] = m.group(1) if m else "<UNPARSED>"
    return out


def sync_file(path: Path, pattern: str, replacement: str, new: str, dry_run: bool) -> bool:
    text = read(path)
    repl = replacement.format(v=new)
    new_text, n = re.subn(pattern, repl, text, flags=re.MULTILINE)
    if n == 0:
        print(f"  !! {path.relative_to(ROOT)}: 未匹配，跳过")
        return False
    if new_text == text:
        return True  # 已是目标，幂等
    if dry_run:
        print(f"  ~ {path.relative_to(ROOT)}: 将改写（dry-run 不落盘）")
    else:
        write(path, new_text)
        print(f"  ✓ {path.relative_to(ROOT)}: {re.search(pattern, text, flags=re.MULTILINE).group(0)} → {repl}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="版本全链同步工具")
    ap.add_argument("--set", metavar="X.Y.Z", help="设置目标版本并全链改写")
    ap.add_argument("--check", action="store_true", help="只读校验全链一致")
    ap.add_argument("--dry-run", action="store_true", help="不落盘，仅报告")
    args = ap.parse_args()

    # Windows GBK 终端无法打印 ✓/✗ 等非 ASCII —— 强制 UTF-8 输出避免编码崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    if not args.set and not args.check:
        ap.error("必须指定 --set X.Y.Z 或 --check")

    versions = current_versions()
    missing = [k for k, v in versions.items() if v in ("<MISSING>", "<UNPARSED>")]
    ok = not missing

    if args.check:
        print("== 版本全链校验 ==")
        for rel, v in versions.items():
            print(f"  {rel}: {v}")
        if missing:
            print(f"  !! 缺失/无法解析: {missing}")
            return 1
        values = [v for v in versions.values() if v != "<MISSING>"]
        if len(set(values)) > 1:
            print(f"  !! 漂移: {values}")
            return 1
        # e2e_v12 契约
        e2e = read(ROOT / "scripts" / "e2e_v12.py")
        hits = re.findall(E2E_PATTERN, e2e)
        if hits and set(hits) != set(values):
            print(f"  !! e2e_v12 契约 {set(hits)} != 全链 {set(values)}")
            return 1
        print(f"  ✓ 全链一致: {values[0] if values else 'N/A'}")
        return 0

    # --set
    new = args.set
    if not SEMVER.match(new):
        print(f"!! 非法版本号 {new!r}，须为 X.Y.Z")
        return 1
    print(f"== 全链同步至 {new} ==")
    for rel, pattern, replacement in TARGETS:
        p = ROOT / rel
        if not p.exists():
            print(f"  !! {rel}: 文件缺失")
            ok = False
            continue
        ok = sync_file(p, pattern, replacement, new, args.dry_run) and ok
    # e2e_v12 契约断言
    e2e_path = ROOT / "scripts" / "e2e_v12.py"
    e2e_text = read(e2e_path)
    new_e2e, n = re.subn(E2E_PATTERN, f'"{new}"', e2e_text)
    if n == 0:
        print(f"  !! {e2e_path.relative_to(ROOT)}: 未匹配版本契约断言")
        ok = False
    elif new_e2e != e2e_text:
        if args.dry_run:
            print(f"  ~ {e2e_path.relative_to(ROOT)}: e2e 契约将改写（dry-run）")
        else:
            write(e2e_path, new_e2e)
            print(f"  ✓ {e2e_path.relative_to(ROOT)}: e2e 契约 → {new}")
    print("== 完成 ==" if ok else "== 存在失败 ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
