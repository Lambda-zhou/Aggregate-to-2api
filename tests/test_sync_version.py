"""scripts/sync_version.py 单测（v22 T2）。

用 tmp_path 模拟全链版本源，验证 --check/--dry-run/--set/UTF-8 行为。
不触碰真实仓库文件（全部在 tmp 副本内跑）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_version.py"
VERSION = r'^version = "([^"]+)"'


@pytest.fixture()
def fake_tree(tmp_path: Path) -> Path:
    """构造最小全链版本树（覆盖 SCRIPT 内 TARGETS 所需路径）。"""
    files = {
        "pyproject.toml": 'version = "20.3.10"\n',
        "deploy/pyproject.toml": 'version = "20.3.10"\n',
        "api/main.py": 'version="20.3.10",\n',
        "frontend/package.json": '{"version": "20.3.10"}\n',
        "landing/package.json": '{"version": "20.3.10"}\n',
        "desktop/package.json": '{"version": "20.3.10"}\n',
        "desktop/src-tauri/tauri.conf.json": '{"version": "20.3.10"}\n',
        "desktop/src-tauri/Cargo.toml": 'version = "20.3.10"\n',
        "README.md": "badge version-20.3.10\n",
        "scripts/e2e_v12.py": 'ver == "20.3.10"\n',
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def run(script_args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**__import__("os").environ, "SYNC_VERSION_ROOT": str(cwd)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *script_args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_check_consistent_ok(fake_tree: Path) -> None:
    """全链一致时 --check 退出 0。"""
    r = run(["--check"], fake_tree)
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"


def test_check_missing_file_fails(fake_tree: Path) -> None:
    """缺失文件时 --check 非零（G15/H1 语义：先报缺失）。"""
    (fake_tree / "desktop" / "src-tauri" / "Cargo.toml").unlink()
    r = run(["--check"], fake_tree)
    assert r.returncode != 0
    assert "MISSING" in r.stdout or "缺失" in r.stdout


def test_set_is_idempotent(fake_tree: Path) -> None:
    """--set 后全链一致；重复 --set 不二次改写（幂等）。"""
    r1 = run(["--set", "21.0.0"], fake_tree)
    assert r1.returncode == 0
    r2 = run(["--check"], fake_tree)
    assert r2.returncode == 0, f"set 后 check 应绿: {r2.stdout}"
    # 二次 set 相同版本，不应再改写（内容不变）
    r3 = run(["--set", "21.0.0"], fake_tree)
    assert r3.returncode == 0
    assert "→" not in r3.stdout or "已是目标" in r3.stdout


def test_dry_run_does_not_write(fake_tree: Path) -> None:
    """--dry-run 报告差异但不落盘。"""
    r = run(["--dry-run", "--set", "22.0.0"], fake_tree)
    assert r.returncode == 0
    # 文件内容未被改写
    text = (fake_tree / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "20.3.10"' in text


def test_set_writes_e2e_contract(fake_tree: Path) -> None:
    """--set 同步改写 e2e_v12.py 契约断言。"""
    r = run(["--set", "21.0.0"], fake_tree)
    assert r.returncode == 0
    e2e = (fake_tree / "scripts" / "e2e_v12.py").read_text(encoding="utf-8")
    assert 'ver == "21.0.0"' in e2e


def test_utf8_output_no_crash(fake_tree: Path) -> None:
    """GBK 编码环境下 --check 不因 ✓/非 ASCII 崩溃（B2 同源问题）。"""
    env_extra = {"PYTHONIOENCODING": "cp936", "SYNC_VERSION_ROOT": str(fake_tree)}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=fake_tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env={**__import__("os").environ, **env_extra},
    )
    assert r.returncode == 0, f"GBK 下不应崩溃: rc={r.returncode} stderr={r.stderr}"
