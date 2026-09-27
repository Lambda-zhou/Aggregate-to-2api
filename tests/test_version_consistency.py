"""版本全链一致性门禁（v21 T1）。

读取全链版本号源并断言彼此一致 + 符合 semver；同时校验 scripts/e2e_v12.py
的契约断言版本与全链一致（防漂移）。CI 首步 fail-fast。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _extract(pattern: str, path: Path, group: int = 1) -> str:
    m = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert m, f"在 {path.relative_to(ROOT)} 中未找到匹配 {pattern!r}"
    return m.group(group)


@pytest.fixture(scope="module")
def version_sources() -> dict[str, str]:
    """全链版本源 → 提取的版本串。"""
    return {
        "pyproject.toml": _extract(r'^version = "([^"]+)"', ROOT / "pyproject.toml"),
        "deploy/pyproject.toml": _extract(r'^version = "([^"]+)"', ROOT / "deploy" / "pyproject.toml"),
        "api/main.py": _extract(r'version="([^"]+)"', ROOT / "api" / "main.py"),
        "frontend/package.json": _extract(r'"version": "([^"]+)"', ROOT / "frontend" / "package.json"),
        "landing/package.json": _extract(r'"version": "([^"]+)"', ROOT / "landing" / "package.json"),
        "desktop/package.json": _extract(r'"version": "([^"]+)"', ROOT / "desktop" / "package.json"),
        "desktop/src-tauri/tauri.conf.json": _extract(
            r'"version": "([^"]+)"', ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
        ),
        "desktop/src-tauri/Cargo.toml": _extract(r'^version = "([^"]+)"', ROOT / "desktop" / "src-tauri" / "Cargo.toml"),
        "README.md": _extract(r"version-([0-9]+\.[0-9]+\.[0-9]+)", ROOT / "README.md"),
    }


def test_all_sources_exist() -> None:
    """全链版本源文件必须存在（H1：防拷贝漏文件——先于提取给清晰失败信息而非裸 assert）。"""
    missing = [
        rel
        for rel in (
            "pyproject.toml",
            "deploy/pyproject.toml",
            "api/main.py",
            "frontend/package.json",
            "landing/package.json",
            "desktop/package.json",
            "desktop/src-tauri/tauri.conf.json",
            "desktop/src-tauri/Cargo.toml",
            "README.md",
        )
        if not (ROOT / rel).exists()
    ]
    assert not missing, f"版本全链缺失文件: {missing}（请先补全再跑版本门禁）"


def test_all_sources_parse_semver(version_sources: dict[str, str]) -> None:
    """每个源都提取到合法的 X.Y.Z。"""
    for name, ver in version_sources.items():
        assert SEMVER.match(ver), f"{name} 版本 {ver!r} 非合法 semver"


def test_all_sources_agree(version_sources: dict[str, str]) -> None:
    """全链版本彼此相等。"""
    values = list(version_sources.values())
    assert len(set(values)) == 1, f"版本漂移: {version_sources}"


def test_e2e_v12_contract_matches(version_sources: dict[str, str]) -> None:
    """scripts/e2e_v12.py 的契约断言版本必须与全链一致（防上次 20.0.0 漂移复发）。"""
    expected = version_sources["api/main.py"]
    e2e = (ROOT / "scripts" / "e2e_v12.py").read_text(encoding="utf-8")
    # 匹配任意 X.Y.Z 契约断言（勿硬编码 20.x——版本升级后 hits 会空导致误报）
    hits = re.findall(r'"(\d+\.\d+\.\d+)"', e2e)
    assert hits, "e2e_v12.py 中未找到版本契约断言"
    for hit in hits:
        assert hit == expected, f"e2e_v12 契约版本 {hit} != 全链 {expected}"
