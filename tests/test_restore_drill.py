"""scripts/restore_drill.py --from-litestream 单测（v22 T4）。

用 tmp_path 造 SQLite 库 + 临时 litestream.yml + 注入 mock litestream 命令
（复制源库到 -o 目标，模拟 restore），验证恢复演练闭环。
不依赖真实 litestream 二进制 / R2 / Docker。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_drill.py"


def _make_db(path: Path, rows: int = 3) -> None:
    """造一个含 requests 表 + N 行的 SQLite 库。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, status TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO requests VALUES (?, ?)", (f"t{i}", "done"))
    conn.commit()
    conn.close()


@pytest.fixture()
def litestream_env(tmp_path: Path):
    """源库 + 配置文件 + mock restore 命令。"""
    src = tmp_path / "data" / "imagefree.db"
    src.parent.mkdir(parents=True, exist_ok=True)
    _make_db(src, rows=5)

    cfg = tmp_path / "litestream.yml"
    cfg.write_text(
        textwrap.dedent(
            f"""\
            dbs:
              - path: {src}
                replicas:
                  - type: local
                    path: {tmp_path / 'replica'}
            """
        ),
        encoding="utf-8",
    )

    # mock litestream 命令：`restore -config <cfg> -o <out> <src>` → 复制 src 到 out
    mock = tmp_path / "mock_litestream.py"
    mock.write_text(
        textwrap.dedent(
            """\
            import shutil, sys
            # 用法: mock_litestream.py restore -config <cfg> -o <out> <src>
            args = sys.argv[1:]
            if "restore" in args:
                src = args[-1]
                out = args[args.index("-o") + 1]
                try:
                    shutil.copy2(src, out)
                except OSError as e:
                    print(f"mock restore failed: {e}", file=sys.stderr)
                    raise SystemExit(1)
            else:
                raise SystemExit(f"unexpected args: {args}")
            """
        ),
        encoding="utf-8",
    )
    return {"src": src, "cfg": cfg, "mock": mock, "cmd": f"{sys.executable} {mock}"}


def _run(script_args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *script_args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_parse_litestream_paths(litestream_env: dict) -> None:
    """_litestream_db_paths 能解析 yml 中 dbs[].path。"""
    from scripts.restore_drill import _litestream_db_paths

    import importlib.util

    spec = importlib.util.spec_from_file_location("rd", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    paths = mod._litestream_db_paths(str(litestream_env["cfg"]))
    assert str(litestream_env["src"]) in paths


def test_drill_from_litestream_ok(litestream_env: dict) -> None:
    """mock restore 后完整性 + 行数验证通过，退出 0。"""
    r = _run(
        [
            "--from-litestream",
            "--litestream-config",
            str(litestream_env["cfg"]),
            "--litestream-cmd",
            litestream_env["cmd"],
        ],
        litestream_env["cfg"].parent,
    )
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
    assert "[OK] imagefree 恢复演练通过" in r.stdout
    assert "requests=5" in r.stdout


def test_drill_from_litestream_bad_source(litestream_env: dict) -> None:
    """restore 失败（源库被删）→ 返回 1 且 stdout 有 FAIL。"""
    litestream_env["src"].unlink()
    r = _run(
        [
            "--from-litestream",
            "--litestream-config",
            str(litestream_env["cfg"]),
            "--litestream-cmd",
            litestream_env["cmd"],
        ],
        litestream_env["cfg"].parent,
    )
    assert r.returncode == 1
    assert "FAIL" in r.stdout


def test_drill_from_litestream_no_db(litestream_env: dict) -> None:
    """yml 无 dbs → 报 FAIL 返回 1。"""
    empty = litestream_env["cfg"].parent / "empty.yml"
    empty.write_text("dbs: []\n", encoding="utf-8")
    r = _run(
        [
            "--from-litestream",
            "--litestream-config",
            str(empty),
            "--litestream-cmd",
            litestream_env["cmd"],
        ],
        empty.parent,
    )
    assert r.returncode == 1
    assert "无可恢复库" in r.stdout


def test_drill_from_litestream_missing_cmd(litestream_env: dict) -> None:
    """litestream 命令不存在 → 返回 1 且提示 --litestream-cmd。"""
    r = _run(
        [
            "--from-litestream",
            "--litestream-config",
            str(litestream_env["cfg"]),
            "--litestream-cmd",
            "definitely-no-such-litestream-bin",
        ],
        litestream_env["cfg"].parent,
    )
    assert r.returncode == 1
    assert "--litestream-cmd" in r.stdout


def test_drill_backup_mode_backward_compat(tmp_path: Path) -> None:
    """--backup-dir 模式保持向后兼容（v20.3.6 行为不变）。"""
    src = tmp_path / "imagefree-20260926-061642.db"
    _make_db(src, rows=2)
    r = _run(["--backup-dir", str(tmp_path)], tmp_path)
    assert r.returncode == 0
    assert "imagefree 恢复演练通过" in r.stdout
    assert "requests=2" in r.stdout