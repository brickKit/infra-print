# infra_print/：入口与组装

组件对外的一个包：外壳 `be/py-render` 列出 `infra_print.definition()`，`python -m infra_print serve|migrate` 单独运行。这里只**组装**：组件定义与配置键（`__init__.py`），`module.py` 持有运行时、版本库与渲染的工作进程池，把路由与系统面登记成转给业务模块的调用，启动时登记随带的模板、停止时停掉工作进程；业务一行也不写。

## 负责与不负责

- 负责：`ID`、`VERSION`（从安装的包读）、端口、渲染上限的配置键（`KEYS`、`OWN`）、资源与操作（`authz()`）、`definition()`；`Print` 的构造（读上限）、`start`、`stop`、`services`、`routes` 与转发。
- 不负责：模板与版本（`templates/`）；渲染（`rendering/`）。

## 文件

| 文件 | 内容 |
| --- | --- |
| `__init__.py` | 组件定义：身份、版本、端口、配置键与说明、资源与操作、`definition()` |
| `__main__.py` | 单独运行的入口 |
| `module.py` | `Print`：组装与转发（上面） |

资源目录（不是代码）：`bundled/`（随带的模板，每个一个目录：`template.typ`、`schema.json`、`meta.json`）、`fonts/`（Noto Sans SC，模板版本的一部分）、`migrations/`（表）。

## 模块

| 包 | 一句话 | 依赖（本组件内） |
| --- | --- | --- |
| `rendering` | 渲染：工作进程池与上限、工作进程、一次渲染（先按版本的 schema 校验视图模型）、系统面 `RenderService.Render`、预览 | `templates` |
| `templates` | 模板与版本：随带的模板与启动时登记、版本的读取与缓存、视图模型的校验、模板与版本的路由 | — |

分层（每个包只引用比它低的层）：`rendering` → `templates`。Python 没有 `internal`：子包只由本包引用；模块从 `infra_print` 取操作的名字（`__init__.py` 不在导入时引用模块，所以不成环）。

## 依赖与被依赖

- 依赖：`templates`、`rendering`；SDK `besdk`；契约包 `be.infra.print.v3`。
- 被依赖：外壳 `be/py-render`、`__main__.py`、`tests/`、`itest/`。

## 测试

`tests/test_contract.py`：清单的 `configSchema` 与事件、OpenAPI 与路由、合并安全（包里全部模块，工作进程除外）。
