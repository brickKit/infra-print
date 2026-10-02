"""两个进程入口（L1，不连库）：服务 ``python -m infra_print.main`` 与迁移
``python -m infra_print.migrate``。brickKit 用同一个镜像跑两者；入口对不认识的参数
必须立即失败，否则写错的迁移参数会把迁移容器变成第二个常驻服务，主服务一直等它。
"""

from __future__ import annotations

import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from infra_print.migrate import build_dsn


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - 跑的是当前解释器与本组件自己的模块
        [sys.executable, "-m", *args], capture_output=True, text=True, timeout=60, env=env
    )


def test_服务入口带参数立即失败() -> None:
    r = _run("infra_print.main", "migrate")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "用法" in r.stderr


def test_迁移入口参数不对退出2() -> None:
    r = _run("infra_print.migrate", "up")
    assert r.returncode == 2
    assert "用法" in r.stderr


def test_迁移入口缺连接配置退出1并点名() -> None:
    r = _run("infra_print.migrate", "apply", env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 1
    for key in ("PG_HOST", "PG_PORT", "PG_DATABASE", "PG_USER", "PG_PASSWORD"):
        assert key in r.stderr


def test_迁移连接串_口令编码_search_path指向PG_SCHEMA() -> None:
    dsn = build_dsn(
        {
            "PG_HOST": "db",
            "PG_PORT": "5432",
            "PG_DATABASE": "brickkit_db",
            "PG_USER": "infra_print_rw",
            "PG_PASSWORD": "p@ss:w/rd%",
            "PG_SCHEMA": "infra_print_x",
        }
    )
    u = urlsplit(dsn)
    assert u.scheme == "postgresql"
    assert (u.hostname, u.port, u.path) == ("db", 5432, "/brickkit_db")
    assert unquote(u.username) == "infra_print_rw"
    assert unquote(u.password) == "p@ss:w/rd%"
    assert parse_qs(u.query)["options"] == ["-csearch_path=infra_print_x"]
