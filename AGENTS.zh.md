[English](AGENTS.md) · [中文](AGENTS.zh.md)

# infra/print

开发本组件的 AI 指南。用法、边界与契约：BRICKKIT.md；为什么这样设计：`docs/design.md`；依赖、配置与部署：component.yaml。

## 代码地图

| 路径 | 负责 |
|---|---|
| `backend/infra_print/module.py` | 唯一入口 `create_module(rt)`：组装 service、FastAPI 应用（`new_fastapi_app`）、gRPC 注册函数与后台循环（outbox 推送 + 两个分区循环）。独立运行与进外壳是同一个函数 |
| `backend/infra_print/main.py` | `run_standalone(create_module)`；带任何参数都拒绝（退出 2） |
| `backend/infra_print/migrate.py` | 迁移容器的入口 `python -m infra_print.migrate apply`：yoyo、`PG_*`、经 libpq `options` 设 `search_path`。唯一读进程环境变量的文件 |
| `backend/infra_print/render/` | 渲染接口（`base.py`）、按通道分流的 `get_renderer`（`__init__.py`）、PDF（`pdf.py`：沙箱 Jinja2 + 只认 `data:` 的 WeasyPrint）与 ZPL（`zpl.py`） |
| `backend/infra_print/repo/templates.py` | 模板与版本：读、批量读、列表、上传、回滚、版本列表 |
| `backend/infra_print/repo/jobs.py` | 一条 `print_jobs` 加 outbox 事件，同一个事务 |
| `backend/infra_print/service/print_service.py` | REST 与 gRPC 共用：正式渲染（记 job）、预览（什么都不记）、结构性校验、版本游标 |
| `backend/infra_print/http/routes.py` | REST 路由，每条都用 `besdk.get`、`besdk.post` 或 `besdk.put` 带权限键注册；错误 → 状态码映射 |
| `backend/infra_print/grpc/server.py` | `infra.print.v1.PrintService` |
| `backend/infra_print/partition.py` | `print_jobs` 的月分区（提前三个月）、`event_outbox` 的周分区（提前四周） |
| `migrations/` | yoyo SQL 迁移及对应的 `.rollback.sql` |
| `contracts/` | proto、OpenAPI、事件 schema |
| `gen/infra/print/v1/` | 生成的 Python 代码（`grpc_tools.protoc`），包名 `infra.print.v1`；不手改。`gen/infra/` 故意没有 `__init__.py` |
| `scripts/check.py` | `make import-scan`、`make module-check` 背后的 ast 扫描 |
| `scripts/seed.sh` | 本地演示数据：`scripts/templates/` 里的模板，再经 gRPC 真渲染一次 |
| `tests/` | pytest，连真实 PostgreSQL、真实 WeasyPrint、真实进程内 gRPC 服务、真实 JWT 与 bundle 轮询（`tests/authz_helpers.py`） |

| 要做的事 | 从这里开始 | 然后 |
|---|---|---|
| 新的 REST 路由 | `contracts/print.openapi.yaml`（只增） | `backend/infra_print/http/routes.py`（`besdk.get` 带 `assembly.yaml` 里的键）、`backend/infra_print/service/print_service.py` |
| 新的 rpc 或字段 | `contracts/infra/print/v1/print.proto` | 重新生成 `gen/`（见构建与测试）、`backend/infra_print/grpc/server.py` |
| 新的列 | `migrations/`（下一个 `00N_*.sql` 及其 rollback） | `backend/infra_print/repo/templates.py` |
| 另一种渲染引擎或通道 | `backend/infra_print/render/base.py`（接口） | `backend/infra_print/render/__init__.py`、`migrations/` 里的 `channel` CHECK、契约里的 enum |
| 渲染安全问题 | `backend/infra_print/render/pdf.py` | `tests/test_render_safety.py` |
| 某个错误回错了状态码 | `backend/infra_print/http/routes.py` | `backend/infra_print/grpc/server.py` |

## 构建与测试

```bash
make venv                    # 建 .venv：Python 3.12、本组件（可编辑安装）与开发工具，输出 "✓ .venv 就绪"
# 测试连测试库 brickkit_test_db，绝不连 brickkit_db（项目根 make test-db-init ID=infra/print 准备）
export TEST_PG_DSN="postgres://postgres:<口令>@localhost:5432/brickkit_test_db?sslmode=disable"
make test                    # "N passed"，没有 "skipped" 行；没设 TEST_PG_DSN 时拒绝运行
make check-version dag-check contract-check import-scan module-check   # 每个目标最后一行是 ✓
make docs-check              # "0 with errors, 0 warnings"
# 迁移，以登录角色运行：
PG_HOST=localhost PG_PORT=5432 PG_DATABASE=brickkit_test_db PG_USER=infra_print_rw \
  PG_PASSWORD=<infra_print_rw 的口令> PG_SCHEMA=infra_print make migrate-idempotent   # ✓ 迁移幂等
```

PDF 测试会调 `/usr/bin/pdftotext`，本机要装 Noto CJK 字体。改了 `.proto` 之后用钉住版本的 `grpcio-tools` 重新生成：

```bash
.venv/bin/python -m grpc_tools.protoc -I contracts \
  -I "$(.venv/bin/python -c 'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')" \
  --python_out=gen --grpc_python_out=gen --pyi_out=gen contracts/infra/print/v1/print.proto
```

`gen/` 与这条命令的产物不一致时 `make contract-check` 失败。真机验证在项目根目录：`make verify ID=infra/print ROUTE=/infra/print/templates FOCUS=1 SEED=1` 构建镜像、只起本组件需要的东西、核对迁移、健康检查与权限判定、灌演示模板、跑一次 focus，最后收尾。`make image` 另外核对镜像里能 import 本组件、有中文字体。

## 设计取舍

- **纯函数，所以没有幂等键。** 同一模板版本加同样的数据，得到同样的字节；`Render` 不带 `idempotency_key`（与别的组件的写命令不同），也没有 `command_idempotency` 表。调用方重试就是再调一次。
- **模板存在数据库里**，版本只增不改；回滚是把旧版本复制成新版本。运行期不读任何模板文件或迁移文件：外壳镜像里没有本仓库的副本。
- **完整的 Jinja2，跑在沙箱里。** 真实单据要按行循环明细，所以不阉割语法；`SandboxedEnvironment` 让表达式摸不到 Python 内部对象。模板里不写业务规则，这一条靠评审守。
- **PDF 渲染什么都不去取。** WeasyPrint 的 `url_fetcher` 只认 `data:` URI；被拒的资源记日志后跳过。
- **两条渲染路径共用一个接口**：HTML → PDF 与模板 → ZPL 只共用 `get_renderer(channel)`。把 WeasyPrint 换成别的引擎只改 `render/pdf.py`。
- **迁移不是模块的一部分**：brickKit 用本镜像跑 `python -m infra_print.migrate apply`；`Module` 不带迁移目录。
- **包名按组件唯一**：代码包 `infra_print`，契约代码 `infra.print.v1` 放在命名空间包 `infra` 下，一个 Python 外壳才能托管多个成员。

## 易错点

| 不要 | 症状 | 原因 |
|---|---|---|
| 给 `Render` 加 `idempotency_key`，或加一张 `command_idempotency` 表 | 什么都不坏；表永远是空的，下一个人照着抄 | 渲染没有值得去重的副作用，重复调用就是再渲染一次 |
| 用普通的 `jinja2.Environment` 渲染模板 | 普通模板的测试都过；能编辑模板的人可以在服务进程里执行代码（在外壳里就是所有成员的进程） | `{{ cycler.__init__.__globals__.os… }}` 能拿到 `os`；`tests/test_render_safety.py` 覆盖 |
| 让 WeasyPrint 用默认的 URL 取数器，或给它 `base_url` | PDF 看起来没问题；`<link rel="attachment" href="file:///proc/self/environ">` 会把进程环境（含数据库口令）嵌进 PDF，`http://` 能打到内部服务 | 只加载 `data:` URI |
| 只装 Pango / Cairo / GDK-Pixbuf，不装 `fonts-noto-cjk`、不跑 `fc-cache -f`（本镜像或托管它的外壳镜像） | 镜像能构建，`/healthz` 是绿的；PDF 里的中文乱码且重复，不是方框 | WeasyPrint 退回 DejaVu |
| 中文模板不写 `font-family: "Noto Sans CJK SC"` | 能渲染，但字形是日文变体 | 没有语言提示时 fontconfig 选 Noto CJK 的 JP 字面 |
| 先插 `print_template_versions` 再 upsert `print_templates` | 新模板第一次保存撞外键 | 版本行引用模板行 |
| 在模块代码里按工作目录或 `__file__` 读文件 | 独立运行没事；进外壳就坏，外壳镜像里没有本仓库的 `migrations/` 和模板 | 只有 `migrate.py` 读 `migrations/`，而且只在本组件自己的镜像里 |
| 加回 `gen/infra/__init__.py`，或把代码包重新叫 `app` | 同一外壳里的第二个 Python 成员覆盖或遮住本组件的包 | `infra` 是命名空间包；`make import-scan` 拒绝 `import app` |
| 用和 `pyproject.toml` 钉的不同版本的 `grpcio-tools` 重新生成 `gen/` | `make contract-check` 在只有生成器输出变了的文件上失败 | 核对是拿现场生成的代码比 |

## 改代码前自查

1. 这件事是渲染（这里），还是打印什么、什么时候打（调用方）？后者永远不进这里，回查数据也不进。
2. 我是不是在加依赖、消费事件、幂等键或行级范围？停下：看设计取舍与 `docs/design.md`。
3. 改动碰到 `render/` 了吗？保住沙箱与只认 `data:` 的取数器；跑 `tests/test_render_safety.py`。
4. 改契约？只增：新字段、rpc、路径或查询参数。用钉住的工具重新生成 `gen/`。
5. 新的 REST 路由用 `besdk.get` / `post` / `put` 带 `assembly.yaml` 里的键注册。
6. 新规则 → 先写会失败的测试（涉及 SQL 的连真实数据库），再写代码。
7. 发版后的第一处改动之前先升 `metadata.version`（以及 `pyproject.toml`）；BRICKKIT.md 与 `docs/design.md` 和代码在同一个提交里更新。
