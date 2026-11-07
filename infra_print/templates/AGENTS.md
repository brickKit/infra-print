# templates：模板与版本

模板与它的版本：随组件提供的模板在启动时按摘要登记为版本（客户自己的当前版本从不被替换）；版本的内容、数据 schema 与摘要读一次就留着（版本不变，当前版本每次都读）；渲染前按版本的 schema 校验视图模型；用户面的模板与版本列表、版本的读取。

## 负责与不负责

- 负责：表 `template`、`template_version`；随带的模板（`infra_print/bundled/`）与摘要（内容、schema 与字体）；`register`；`Store`、`Version.validate`（`VALIDATION_FAILED`）；路由的处理：模板列表、版本列表、版本的读取、在线编辑（本版不做）。
- 不负责：渲染与预览（`rendering`）；路由的登记（入口的 `module.py`）。

## 文件

| 文件 | 内容 |
| --- | --- |
| `__init__.py` | 包的说明 |
| `bundled.py` | `FONTS`、`fonts_digest`、`digest`、`Bundled`、`bundled`、`register` |
| `store.py` | `ERR_VALIDATION`、`ERR_NO_TEMPLATE`、`Version`（`validate`）、`Store`（`version`：0 是当前版本） |
| `routes.py` | `TEMPLATE_NAME`、列表的定义、`path`（路由的模板与版本）、`list_templates`、`list_versions`、`get_version`、`new_version` |

## 依赖与被依赖

- 依赖：SDK `besdk`；`infra_print` 的操作名。
- 被依赖：`rendering`（`Store`、`path`、`TEMPLATE_NAME`）；入口。

## 规则在哪

`docs/design.md` 的模板与版本；装配项目协议 10。

## 测试

`tests/test_rendering.py`（视图模型的校验）；`itest/test_print_integration.py`（启动时登记、列表与读取）。
