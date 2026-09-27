"""SQLite 备份恢复演练脚本（非破坏性，J2 v20.3.6 + v22 P0-1 litestream）。

目的：每周自动验证「备份真实可恢复」，不做生产覆盖（区别于 restore_db.py 的覆盖恢复）。
两种数据源：
1. --backup-dir：最新本地备份 → 临时目录 → 打开 + PRAGMA integrity_check + 行数 → 清理（v20.3.6）
2. --from-litestream：从 litestream 副本 restore 到临时目录 → 同验证（v22 P0-1，RPO 秒级副本的恢复演练）
无任何生产 DB 写入，纯只读演练。

用法:
    python scripts/restore_drill.py --backup-dir /opt/imagefree-api/backups
    python scripts/restore_drill.py --from-litestream --litestream-config deploy/litestream.yml
    # cron 每周日 04:00:
    # 0 4 * * 0 cd /opt/imagefree-api && .venv/bin/python scripts/restore_drill.py --backup-dir /opt/imagefree-api/backups >> /opt/imagefree-api/backups/drill.log 2>&1
    # litestream 模式（本地 type:local 或生产 R2 均可，--litestream-cmd 可注入测试）:
    # 0 5 * * 0 cd /opt/imagefree-api && .venv/bin/python scripts/restore_drill.py --from-litestream --litestream-config deploy/litestream.yml
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


def _latest_backup(backup_dir: str, db_name: str = "imagefree") -> str | None:
    """取指定 DB 的最新备份（按时间戳排序，imagefree-20260926-061642.db）。"""
    pat = os.path.join(backup_dir, f"{db_name}-*.db")
    files = sorted(glob.glob(pat), reverse=True)
    return files[0] if files else None


def _verify_db(db_file: str, db_name: str) -> int:
    """对恢复出的 db 文件做完整性 + 主表行数验证。返回 0=成功 / 1=失败。"""
    conn = sqlite3.connect(db_file)
    try:
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        # 主表存在则查行数；不存在（非主库）只校验完整性
        try:
            rows = conn.execute("SELECT count(*) FROM requests").fetchone()[0]
            row_info = f" requests={rows}"
        except sqlite3.Error:
            row_info = " (无 requests 表，跳过行数)"
        if integ != "ok":
            print(f"[FAIL] {db_name} 恢复演练失败: integrity={integ}（{db_file}）")
            return 1
        print(f"[OK] {db_name} 恢复演练通过: integrity=ok{row_info}（{db_file}）")
        return 0
    finally:
        conn.close()


def drill(backup_dir: str, db_name: str = "imagefree") -> int:
    """从最新备份恢复到临时目录并验证可查询。返回 0=成功 / 1=失败。"""
    latest = _latest_backup(backup_dir, db_name)
    if not latest:
        print(f"[SKIP] 无 {db_name} 备份（backup_dir={backup_dir}）")
        return 0
    tmp = tempfile.mkdtemp(prefix="restore-drill-")
    try:
        target = os.path.join(tmp, f"{db_name}.db")
        shutil.copy2(latest, target)
        return _verify_db(target, db_name)
    except Exception as e:
        print(f"[FAIL] {db_name} 恢复演练异常: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _litestream_db_paths(config: str) -> list[str]:
    """解析 litestream.yml 中 dbs[].path 列表（本地或容器内源库路径，供 restore 用）。"""
    try:
        import yaml

        with open(config, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return [d["path"] for d in data.get("dbs", []) if isinstance(d, dict) and d.get("path")]
    except Exception:
        # 无 pyyaml 时正则 fallback：`- path: <value>`（支持引号）
        text = open(config, encoding="utf-8").read()
        paths = re.findall(r"^\s*-\s+path:\s*(.+?)\s*$", text, re.M)
        return [p.strip().strip("\"'") for p in paths if p.strip()]


def drill_from_litestream(config: str, litestream_cmd: str = "litestream") -> int:
    """从 litestream 副本 restore 全部库到临时目录并验证。返回失败计数。

    litestream_cmd 可注入（测试用脚本模拟 restore），默认取 PATH 内 litestream。
    仅读副本、写临时目录，不动生产库。
    """
    paths = _litestream_db_paths(config)
    if not paths:
        print(f"[FAIL] litestream 配置无可恢复库: {config}")
        return 1
    tmp = tempfile.mkdtemp(prefix="litestream-restore-drill-")
    failed = 0
    try:
        # Windows 不能直接执行 .py——litestream_cmd 支持 shlex.split（如 "py C:\\x\\mock.py"）。
        # posix=False 保留 Windows 反斜杠路径（否则 \P 被当转义吞掉）。
        cmd = shlex.split(litestream_cmd, posix=False)
        for src_path in paths:
            db_name = os.path.splitext(os.path.basename(src_path))[0] or "db"
            out = os.path.join(tmp, f"{db_name}.db")
            try:
                subprocess.run(
                    [*cmd, "restore", "-config", config, "-o", out, src_path],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                )
            except subprocess.CalledProcessError as e:
                print(f"[FAIL] {db_name} litestream restore 失败: rc={e.returncode} {e.stderr[:300]}")
                failed += 1
                continue
            except FileNotFoundError:
                print(f"[FAIL] 未找到 litestream 命令 '{litestream_cmd}'（--litestream-cmd 可指定）")
                return 1
            except subprocess.TimeoutExpired:
                print(f"[FAIL] {db_name} litestream restore 超时（>600s）")
                failed += 1
                continue
            failed += _verify_db(out, db_name)
        return failed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite 备份恢复演练（非破坏性）")
    ap.add_argument("--backup-dir", help="备份目录（含 <db>-<ts>.db，backup-dir 模式用）")
    ap.add_argument("--dbs", default="imagefree", help="逗号分隔要演练的 DB 名（backup-dir 模式，默认 imagefree）")
    ap.add_argument("--from-litestream", action="store_true", help="从 litestream 副本恢复演练（v22 P0-1）")
    ap.add_argument("--litestream-config", default="deploy/litestream.yml", help="litestream 配置（含 dbs[].path）")
    ap.add_argument("--litestream-cmd", default="litestream", help="litestream 命令（测试可注入 mock）")
    args = ap.parse_args()

    if args.from_litestream:
        failed = drill_from_litestream(args.litestream_config, args.litestream_cmd)
        print(f"[DONE] litestream 恢复演练完成: {'全部通过' if failed == 0 else f'{failed} 个失败'}")
        return 1 if failed else 0

    if not args.backup_dir:
        ap.error("必须指定 --backup-dir 或 --from-litestream")
    failed = 0
    for db in [d.strip() for d in args.dbs.split(",") if d.strip()]:
        failed += drill(args.backup_dir, db)
    print(f"[DONE] 恢复演练完成: {'全部通过' if failed == 0 else f'{failed} 个失败'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
