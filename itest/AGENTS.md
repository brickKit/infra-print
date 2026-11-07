# itest/：集成测试与测试索引

打印组件对着真实的 PostgreSQL、经它真实的接口的测试（L2）：用户面带 be-sdk-python `besdk.testing` 的令牌，系统面以 `erp/billing`、`erp/sales` 的服务身份调用。标记 `integration`（默认的 `pytest` 不跑）。

## 怎样跑

- `make test-integration`（`uv run pytest -q -m integration itest/`），几秒。
- 环境变量：`BE_SDK_TEST_PG_ADMIN`（PostgreSQL 超级用户的 DSN，例如 `postgres://postgres:<口令>@localhost:5432/postgres`，口令是装配项目 `.env` 的 `POSTGRES_PASSWORD`）；装配项目的 `dev/rebuild/experiment-scripts/itenv.sh` 不打印地设好。
- 前提：库 `brickkit_test_db`；schema `infra_print_it` 每次开始时删掉再建，跑完留着。

## 文件

| 文件 | 测试 | 测的流程 | 被测的模块 |
| --- | --- | --- | --- |
| `test_print_integration.py` | `test_print_end_to_end` | 启动时登记随带的模板（只一次，从不覆盖客户的版本）、模板列表与版本的读取、预览、部署内的组件以服务身份调用 `RenderService.Render`、新的随带版本成为当前版本（当前版本是随带的时） | `templates`、`rendering`、入口 |
| `test_print_integration.py` | `test_a_full_recoverys_checks` | 恢复工具问模板是否还在（当前版本答“还在”，更高的版本答“更低”，不存在的答“不在”）、SDK 自带的检查、别的组件被拒（3.0.22 起） | 入口 `recovery_checks` |

共用的：`tests/sample.py`（账单的样例视图模型，单元测试也用）。

## 加测试

新流程加一个文件或一个测试，标 `@pytest.mark.integration`，在上表加一行；测试不为了通过而改。
