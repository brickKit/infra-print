"""测试夹具——DSN/NATS URL 读取约定与 be-sdk-python 自身测试逐字对应
（``TEST_PG_DSN``/``TEST_NATS_URL``，未设置就跳过，不是放宽断言）。
"""

from __future__ import annotations

import os
import time

import pytest


def dsn() -> str:
    v = os.environ.get("TEST_PG_DSN")
    if not v:
        pytest.skip("未设置 TEST_PG_DSN，跳过（本地至少跑一次真的）")
    return v


def nats_url() -> str:
    return os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


def unique(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"
