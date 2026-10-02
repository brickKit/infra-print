"""迁移入口：``python -m infra_print.migrate apply|rollback``，即 component.yaml
的 ``migration.command``。brickKit 用本组件自己的镜像起一次性容器跑它，
工作目录是镜像的 ``/app``（``migrations/`` 就在那里）；它不经过
``besdk.Runtime``，所以是本组件里唯一读进程环境变量的文件。

连接信息读 ``PG_*``（与 configSchema 同一组键），登录角色就是 ``PG_USER``
（``infra_print_rw``）：表与 yoyo 自己的记账表（``_yoyo_log``、
``_yoyo_version``、``_yoyo_migration``、``yoyo_lock``）都建在 ``PG_SCHEMA``
里、属主是登录角色。schema 隔离靠 libpq 的 ``options=-csearch_path=<schema>``
连接参数。

退出码：缺连接配置 1（点名缺哪些键），参数不对 2，迁移本身失败时 yoyo /
psycopg2 的异常原样抛出（非零退出）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

from besdk import Config, pg_dsn
from yoyo import get_backend, read_migrations

# 相对工作目录：迁移只在本组件镜像（WORKDIR /app）或组件仓库根目录里跑。
_MIGRATIONS_DIR = Path("migrations")
_DEFAULT_SCHEMA = "infra_print"


def build_dsn(env: dict[str, str]) -> str:
    """``PG_*`` → yoyo 的 ``postgresql://`` 连接串（psycopg2 驱动）。缺键抛
    ``ValueError``，消息里列出全部缺的键。"""
    schema = env.get("PG_SCHEMA") or _DEFAULT_SCHEMA
    options = quote(f"-csearch_path={schema}", safe="")
    return f"{pg_dsn(Config(env))}?options={options}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ("apply", "rollback"):
        print(f"用法：python -m infra_print.migrate apply|rollback（收到 {args}）", file=sys.stderr)
        return 2
    try:
        dsn = build_dsn(dict(os.environ))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    backend = get_backend(dsn)
    migrations = read_migrations(str(_MIGRATIONS_DIR))
    with backend.lock():
        if args[0] == "apply":
            backend.apply_migrations(backend.to_apply(migrations))
        else:
            backend.rollback_migrations(backend.to_rollback(migrations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
