"""迁移执行器——被 component.yaml 的 ``migration.command: ["python",
"-m", "app.migrate", "apply"]`` 调用（装配阶段，一次性迁移容器，不是
``run_standalone`` 的常驻进程）。

⚠️ 这个文件在"零 os.Getenv"铁律之外——它是"装配"不是"模块"，同 Go 版
``backend/cmd/migrate/main.go`` 的既有先例（迁移执行器直接读进程环境
变量，因为它根本不经过 ``besdk.Runtime``）。

⚠️ 用 yoyo 的 Python API（``get_backend``/``read_migrations``）直接调，
不 shell 出去调 yoyo 的 CLI 二进制——同 Go 版 migrate.go 直接调
``golang-migrate`` 库而不是 shell 一个 CLI 的既有判据。

⚠️ schema 隔离走 libpq 的 ``options=-csearch_path%3D<schema>`` 连接参数
（真机验证过：yoyo 自己的内部记账表 ``_yoyo_log``/``_yoyo_version``/
``yoyo_lock`` 也会正确落在这个 schema 里，不需要额外传
``--migration-table``）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

from yoyo import get_backend, read_migrations

# ⚠️ 相对 CWD，不是相对 __file__——同 app/module.py 的既有说明：
# ``pip install .``（非 editable）之后 __file__ 会指向 site-packages，
# 往上反推目录就不再是仓库根。CWD 由调用方（brickkit 的一次性迁移容器、
# 本地开发时的项目根目录）保证是仓库根，同 component.yaml 的既有约定。
_MIGRATIONS_DIR = Path("migrations")


def _must_getenv(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        print(f"必需的环境变量 {key} 未设置", file=sys.stderr)
        sys.exit(1)
    return v


def _build_dsn() -> str:
    host = _must_getenv("DATABASE_HOST")
    port = _must_getenv("DATABASE_PORT")
    user = _must_getenv("DATABASE_USER")
    password = _must_getenv("DATABASE_PASSWORD")
    name = _must_getenv("DATABASE_NAME")
    schema = os.environ.get("PG_SCHEMA", "infra_print")
    options = quote(f"-csearch_path={schema}")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}?options={options}"


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("apply", "rollback"):
        print("用法：python -m app.migrate apply|rollback", file=sys.stderr)
        sys.exit(1)

    backend = get_backend(_build_dsn())
    migrations = read_migrations(str(_MIGRATIONS_DIR))

    with backend.lock():
        if sys.argv[1] == "apply":
            backend.apply_migrations(backend.to_apply(migrations))
        else:
            backend.rollback_migrations(backend.to_rollback(migrations))


if __name__ == "__main__":
    main()
