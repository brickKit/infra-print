# rendering：渲染

渲染在工作进程里，不占事件循环：同时渲染的个数、排队期限、超时杀掉、死了换新、输出上限（`pool.py`）；一个工作进程先设地址空间上限再载入 Typst，碰不到文件、包与网络（`worker.py`）；一次渲染先按版本的 schema 校验视图模型，PDF 的创建时间固定，同一版本同一数据逐字节相同；系统面 `RenderService.Render` 与用户面的预览（`render.py`）。

## 负责与不负责

- 负责：`Limits`、`Renderer`（`RENDER_LIMIT_EXCEEDED`、`RENDER_FAILED`、`SERVICE_BUSY`）；工作进程（`python -m infra_print.rendering.worker`）；`render`、`render_call`、`preview`。
- 不负责：版本的读取与校验（`templates`）；路由与系统面的登记（入口）。

## 文件

| 文件 | 内容 |
| --- | --- |
| `__init__.py` | 包的说明 |
| `pool.py` | `WORKER_ENV`、`Limits`、`_Worker`、`Renderer`（工作进程池） |
| `worker.py` | 一个工作进程：读请求帧、渲染、写应答帧（单独的进程，不在合并安全的检查里） |
| `render.py` | `TIMESTAMP`、`MAX_DATA`；`render`（校验后渲染）、`render_call`（`RenderService.Render`）、`preview` |

## 依赖与被依赖

- 依赖：`templates`（`Store`、`path`、`TEMPLATE_NAME`）；SDK `besdk`；契约包。
- 被依赖：入口。

## 规则在哪

`docs/design.md` 的“资源上限”与“渲染引擎”；装配项目协议 10、协议 17（资源上限）。

## 测试

`tests/test_rendering.py`（确定性、上限、不读文件不下载包）；`itest/test_print_integration.py`（预览、系统面的渲染）。
