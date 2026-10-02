"""服务入口：``python -m infra_print.main``（镜像的 ENTRYPOINT、mode: local 的 runCommand）。
不接受任何参数：迁移是另一个入口（infra_print.migrate），参数写错要立即失败，而不是
悄悄起成一个服务。"""

import sys

from besdk import run_standalone

from infra_print.module import create_module

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(f"用法：python -m infra_print.main（不带参数；迁移用 python -m infra_print.migrate apply），收到 {sys.argv[1:]}", file=sys.stderr)
        sys.exit(2)
    run_standalone(create_module)
