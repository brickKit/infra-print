ID      := infra/print
REPO    := infra-print
VERSION := $(shell grep -m1 -E '^  version:' component.yaml | awk '{print $$2}')
# 项目根：本组件在项目里固定挂在 components/<scope>/<name>/ 下
ROOT    := ../../..
# 开发用虚拟环境（make venv 建）；别的位置用 make test VENV=<目录>
VENV    ?= .venv
PYTHON  := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help all venv check-version test migrate-idempotent dag-check contract-check import-scan module-check docs-check smoke image seed

help:  ## 列出所有目标
	@awk 'BEGIN{FS=":.*##"; printf "\n用法: make <目标>\n\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2} \
	     /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0,5)}' $(MAKEFILE_LIST)
	@echo ""

##@ 汇总
all: check-version test migrate-idempotent dag-check contract-check import-scan module-check docs-check  ## 全部门禁（不含要起容器的 smoke / image）

##@ 开发环境
venv:  ## 建 $(VENV)（Python 3.12）并以可编辑方式装上本组件与开发依赖；mode: local / focus 用的就是它
	uv venv --seed -p 3.12 $(VENV)
	$(PYTHON) -m pip install -q -e '.[dev]'
	@echo "✓ $(VENV) 就绪"

##@ 门禁
check-version:  ## metadata.version、pyproject.toml 的 version、HEAD 上的 tag 三者一致
	@test -n "$(VERSION)" || { echo "✗ component.yaml 里读不到 metadata.version"; exit 1; }
	@py="$$(grep -m1 -E '^version = ' pyproject.toml | sed -E 's/^version = "([^"]+)"/\1/')"; \
	 [ "$$py" = "$(VERSION)" ] || { echo "✗ pyproject.toml 的 version（$$py）与 component.yaml 的 $(VERSION) 不一致"; exit 1; }
	@tags="$$(git tag --points-at HEAD | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$$' || true)"; \
	 if [ -n "$$tags" ]; then \
	   [ "$$tags" = "$(VERSION)" ] || { echo "✗ HEAD 上的版本 tag（$$(echo $$tags)）应恰好是 $(VERSION)：Python 组件只有不带 v 的一个 tag"; exit 1; }; \
	 fi
	@echo "✓ version=$(VERSION)（pyproject.toml 一致；HEAD 上没有 tag，或恰好是 $(VERSION)）"

test:  ## L1–L3 测试。需要 TEST_PG_DSN（指向 brickkit_test_db）；TEST_NATS_URL 可选
	@# 连库测试在没有 TEST_PG_DSN 时 pytest.skip，pytest 照样退出 0——全部跳过却显示通过。
	@# 所以这里先拦住，大声失败。
	@test -n "$$TEST_PG_DSN" || { echo "✗ 没设 TEST_PG_DSN：连库测试会全部 SKIP 而显示通过（设法见 AGENTS.md 的 Build and test）"; exit 1; }
	@test -x "$(PYTHON)" || { echo "✗ 没有 $(PYTHON)：先 make venv（或 make test VENV=<已装好的虚拟环境>）"; exit 1; }
	$(PYTHON) -m pytest -q -rs

migrate-idempotent:  ## 同一份迁移连跑两次都成功。需要 PG_HOST PG_PORT PG_DATABASE PG_USER PG_PASSWORD PG_SCHEMA（以 infra_print_rw 登录）
	@# 例：PG_HOST=localhost PG_PORT=5432 PG_DATABASE=brickkit_test_db PG_USER=infra_print_rw
	@#     PG_PASSWORD=… PG_SCHEMA=infra_print make migrate-idempotent
	@test -x "$(PYTHON)" || { echo "✗ 没有 $(PYTHON)：先 make venv"; exit 1; }
	@$(PYTHON) -m infra_print.migrate apply && $(PYTHON) -m infra_print.migrate apply && echo "✓ 迁移幂等"

dag-check:  ## 没有任何依赖：本组件是纯函数，数据由调用方整个传入，同步图上只有入边
	@n="$$(python3 -c 'import yaml; m = yaml.safe_load(open("component.yaml")); print(len((m.get("dependencies") or {}).get("components") or []))')"; \
	 [ "$$n" = 0 ] || { echo "✗ dependencies.components 应为空（实际 $$n 条）：渲染所需的数据由调用方传入，本组件不回查任何组件"; exit 1; }
	@echo "✓ 无依赖，无环"

contract-check:  ## proto 只增不改（buf lint + buf breaking，对比上一个发布 tag），且 gen/ 是当前 .proto 的生成结果
	@# 对比 main 抓不住已经提交到 main 上的破坏性改动（在 main 上工作时等于自己比自己），
	@# 所以对比上一个组件版本 tag；找不到 tag 时才回落到 main，看打印的那一行。
	buf lint
	@base="$$(git describe --tags --abbrev=0 --exclude 'gen/*' 2>/dev/null)"; \
	 against="$${base:+.git#tag=$$base}"; against="$${against:-.git#branch=main}"; \
	 echo "buf breaking --against '$$against'"; buf breaking --against "$$against"
	@test -x "$(PYTHON)" || { echo "✗ 没有 $(PYTHON)：先 make venv（gen/ 比对要用 grpcio-tools）"; exit 1; }
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	 inc="$$($(PYTHON) -c 'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')" \
	   || { echo "✗ 找不到 grpc_tools"; exit 1; }; \
	 $(PYTHON) -m grpc_tools.protoc -I contracts -I "$$inc" --python_out="$$tmp" --grpc_python_out="$$tmp" --pyi_out="$$tmp" \
	   contracts/infra/print/v1/print.proto || { echo "✗ grpc_tools.protoc 失败"; exit 1; }; \
	 diff -r -x __init__.py -x __pycache__ "$$tmp/infra" gen/infra >/dev/null \
	   || { echo "✗ gen/ 不是当前 .proto 的生成结果（改了 .proto 没重新生成，或手改了生成物）："; \
	        diff -rq -x __init__.py -x __pycache__ "$$tmp/infra" gen/infra; exit 1; }
	@echo "✓ gen/ 与 contracts/ 一致"

import-scan:  ## 只 import SDK、第三方库、自己与自己的契约包
	@test -x "$(PYTHON)" || { echo "✗ 没有 $(PYTHON)：先 make venv"; exit 1; }
	@$(PYTHON) scripts/check.py import-scan

module-check:  ## 模块能被合进外壳：入口签名对、零 os.environ、零进程级初始化、栈合规（main.py / migrate.py 是装配代码，不扫）
	@test -x "$(PYTHON)" || { echo "✗ 没有 $(PYTHON)：先 make venv"; exit 1; }
	@$(PYTHON) scripts/check.py module-check

docs-check:  ## 清单与文档的严格 lint（= 项目根 make docs-check ID=$(ID)）
	@cd $(ROOT) && brickkit lint --strict $(ID)

##@ 要起容器的检查（在项目里跑）
smoke:  ## 项目能为本组件生成部署文件（brickkit up --dry-run）
	@cd $(ROOT) && brickkit up --dry-run >/dev/null && echo "✓ smoke：up --dry-run 通过"

image:  ## brickkit build 本组件，并确认镜像里有 sh + wget、component.yaml、能 import 本组件、有中文字体
	@cd $(ROOT) && brickkit build $(ID)
	@docker run --rm --entrypoint sh $(REPO):$(VERSION) -c 'wget --version >/dev/null && test -f /app/component.yaml && test -d /app/migrations' \
	  || { echo "✗ 镜像 $(REPO):$(VERSION) 缺 sh / wget / component.yaml / migrations"; exit 1; }
	@docker run --rm --entrypoint python $(REPO):$(VERSION) -c 'import infra_print.module, infra_print.migrate, infra.print.v1.print_pb2_grpc' \
	  || { echo "✗ 镜像里 import infra_print / 契约包失败"; exit 1; }
	@docker run --rm --entrypoint sh $(REPO):$(VERSION) -c 'fc-list | grep -q "Noto Sans CJK SC"' \
	  || { echo "✗ 镜像里没有 Noto Sans CJK SC：中文 PDF 会乱码"; exit 1; }
	@echo "✓ 镜像 $(REPO):$(VERSION)：sh + wget + component.yaml + migrations、import 正常、有中文字体"

##@ 本地开发数据（只给本地 / 演示用，不进部署与 CI）
seed:  ## 灌默认送货单 sales.delivery_note（两个版本）与发货标签 sales.shipping_label，并经 gRPC 真渲染一次。幂等；要求本组件已经 brickkit up 起来
	@bash scripts/seed.sh
