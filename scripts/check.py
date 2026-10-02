"""组件门禁里要读 Python 源码的两项：import-scan 与 module-check（Makefile 调用）。

用 ast 而不是 grep：注释与文档字符串里解释"为什么不许 os.environ"的文字不会被
误判，真正的调用也不会因为换行、别名而漏掉。任何文件解析失败都直接失败，不会
"什么都没扫到"却打印 ✓。

    python scripts/check.py import-scan
    python scripts/check.py module-check
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PKG = Path("backend/infra_print")
# 装配代码：独立进程的入口，不会被合进外壳，可以读进程环境、可以退出进程。
ASSEMBLY = {PKG / "main.py", PKG / "migrate.py"}

# 本项目九个域下别的组件的顶层包名形如 <domain>_<name>，契约包形如 <domain>.<name>.
DOMAINS = ("mdm", "erp", "crm", "hrm", "prj", "ana", "infra", "integration", "frontend")
OTHER_COMPONENT = re.compile(rf"^({'|'.join(DOMAINS)})_[a-z0-9_]+$")
OWN = {"infra_print"}
OWN_CONTRACT = "infra.print"

# 进程只有一份的东西：模块碰了，进外壳后成员互相覆盖。
FORBIDDEN_CALLS = {
    "os.getenv": "读进程环境变量（进外壳后成员互相覆盖；配置只从 rt.config 读）",
    "os.putenv": "改进程环境变量",
    "sys.exit": "退出整个进程（外壳里所有成员一起下线；返回错误）",
    "os._exit": "退出整个进程",
    "signal.signal": "装信号处理器（进程级）",
    "logging.basicConfig": "初始化全局日志（用 rt.logger）",
    "trace.set_tracer_provider": "设全局 TracerProvider（用 rt.tracer）",
    "metrics.set_meter_provider": "设全局 MeterProvider（用 rt.meter）",
    "uvicorn.run": "自己监听端口（由 run_standalone / 外壳监听）",
    "asyncpg.create_pool": "自己开连接池（用 rt.db）",
    "nats.connect": "自己连 NATS（用 rt.nats）",
    "grpc.aio.server": "自己起 gRPC 服务（交回 register_grpc）",
    "FastAPI": "自己装配 FastAPI（用 besdk.new_fastapi_app(rt)，否则丢掉 SDK 的中间件）",
    "fastapi.FastAPI": "自己装配 FastAPI",
    "prometheus_client.start_http_server": "自己暴露指标端口",
}
FORBIDDEN_ATTRS = {"os.environ": "读进程环境变量（进外壳后成员互相覆盖）"}
# 锁定栈之外的库（模块代码；psycopg2 只给迁移入口用）。
FORBIDDEN_IMPORTS = ("flask", "django", "sqlalchemy", "psycopg2", "psycopg", "tornado", "aiohttp")
ENTRY = re.compile(r"^async def create_module\(rt: Runtime\) -> Module:", re.M)


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _sources() -> list[Path]:
    files = sorted(PKG.rglob("*.py"))
    if not files:
        sys.exit(f"✗ {PKG} 下没有 .py 文件，没法扫")
    return files


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        sys.exit(f"✗ 解析 {path} 失败：{exc}")


def _imports(tree: ast.Module) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.lineno, node.module))
    return out


def import_scan() -> int:
    bad: list[str] = []
    for path in _sources():
        for line, mod in _imports(_parse(path)):
            top = mod.split(".")[0]
            if top in OWN:
                continue
            if top == "app":
                bad.append(f"{path}:{line}: import {mod}（业务包已改名 infra_print；顶层 app 在外壳里会和别的成员撞名）")
            elif OTHER_COMPONENT.match(top):
                bad.append(f"{path}:{line}: import {mod}（别的组件的代码）")
            elif top in DOMAINS and "." in mod and not (mod == OWN_CONTRACT or mod.startswith(OWN_CONTRACT + ".")):
                bad.append(f"{path}:{line}: import {mod}（别的组件的契约生成代码；本组件没有依赖）")
    if bad:
        print("✗ import 了别的组件：\n  " + "\n  ".join(bad))
        return 1
    print("✓ 无组件间 import")
    return 0


def module_check() -> int:
    module_py = PKG / "module.py"
    if not ENTRY.search(module_py.read_text(encoding="utf-8")):
        print(f"✗ {module_py} 里没有 `async def create_module(rt: Runtime) -> Module:`（外壳按这个签名装配成员）")
        return 1
    bad: list[str] = []
    for path in _sources():
        if path in ASSEMBLY:
            continue
        tree = _parse(path)
        for line, mod in _imports(tree):
            if mod.split(".")[0] in FORBIDDEN_IMPORTS:
                bad.append(f"{path}:{line}: import {mod}（锁定栈之外的库）")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                if name in FORBIDDEN_CALLS:
                    bad.append(f"{path}:{node.lineno}: {name}()——{FORBIDDEN_CALLS[name]}")
                if name.endswith("add_signal_handler"):
                    bad.append(f"{path}:{node.lineno}: {name}()——装信号处理器（进程级）")
            elif isinstance(node, ast.Attribute):
                name = _dotted(node)
                if name in FORBIDDEN_ATTRS:
                    bad.append(f"{path}:{node.lineno}: {name}——{FORBIDDEN_ATTRS[name]}")
    if bad:
        print("✗ 模块代码做了只有进程入口才能做的事：\n  " + "\n  ".join(bad))
        return 1
    print("✓ 入口签名对、零 os.environ、零进程级初始化、栈合规")
    return 0


if __name__ == "__main__":
    cmds = {"import-scan": import_scan, "module-check": module_check}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(f"用法：python scripts/check.py {'|'.join(cmds)}")
    sys.exit(cmds[sys.argv[1]]())
