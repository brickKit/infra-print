IMAGE   := brickenterprise/infra-print
VERSION := $(shell grep -E '^\s+version:' component.yaml | head -1 | awk '{print $$2}')
PYTHON  := .venv/bin/python3

.DEFAULT_GOAL := help
.PHONY: help all check-version test image migrate-idempotent dag-check contract-check import-scan module-check docs-check smoke seed

help:  ## 列出所有目标
	@awk 'BEGIN{FS=":.*##"; printf "\n用法: make <目标>\n\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2} \
	     /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0,5)}' $(MAKEFILE_LIST)
	@echo ""

##@ 汇总
all: check-version test image migrate-idempotent dag-check contract-check import-scan module-check docs-check  ## 8 个门禁（不含 smoke，它要真起容器）

##@ 9 个门禁
check-version:  ## component.yaml 的 version、git tag、deployment.image 三者不许分叉（§9.1 两个真相源）
	@tag="$$(git describe --tags --exact-match 2>/dev/null || true)"; \
	 if [ -n "$$tag" ] && [ "$$tag" != "v$(VERSION)" ]; then \
	   echo "✗ git tag $$tag 与 component.yaml 的 $(VERSION) 不一致"; exit 1; fi; \
	 img_ver="$$(grep -E '^[[:space:]]+image:' component.yaml | head -1 | sed -E 's#.*:([0-9]+\.[0-9]+\.[0-9]+)[[:space:]]*$$#\1#')"; \
	 if [ "$$img_ver" != "$(VERSION)" ]; then \
	   echo "✗ deployment.image 的 tag ($$img_ver) 与 component.yaml 的 version ($(VERSION)) 不一致"; exit 1; fi; \
	 echo "✓ version=$(VERSION)（git tag 与 deployment.image 一致）"

test:  ## 需要 TEST_PG_DSN/TEST_NATS_URL，缺省时连库测试自动跳过
	$(PYTHON) -m pytest -v

image:  ## 建镜像并确认里面有 sh + wget（§12.3.7 健康检查需要）
	docker build -t $(IMAGE):$(VERSION) .
	@docker run --rm --entrypoint sh $(IMAGE):$(VERSION) -c 'wget --version >/dev/null' \
	  && echo "✓ 镜像里有 sh + wget"

migrate-idempotent:  ## 同一份迁移连跑两次都必须成功（§13.3 铁律五）
	@# 需要 DATABASE_HOST/PORT/USER/PASSWORD/NAME + PG_SCHEMA（平台真实注入
	@# 的分离变量契约）。本地跑迁移要用 postgres 超级用户（不是
	@# infra_print_rw）：建分区之类的 DDL 需要建表权限。
	@$(PYTHON) -m app.migrate apply && $(PYTHON) -m app.migrate apply && echo "✓ 迁移幂等"

dag-check:  ## 强依赖图无环（§4.2）。零依赖，天然无环——本组件在同步图上只有入边（设计计划 §1.1：纯函数，不回查任何业务库）
	@if ! grep -qE '^\s*components:\s*\[\]' component.yaml; then \
	   echo "✗ infra-print 依赖必须为空（本组件是纯函数，不该依赖任何业务组件）"; exit 1; fi
	@echo "✓ 零依赖，无环"

contract-check:  ## 禁破坏性变更（§8.5、决策 33）
	buf lint
	buf breaking --against '.git#branch=main'

import-scan:  ## 铁律六：不许 import 任何其他组件仓库
	@bad=$$(grep -rlE "^from (mdm|erp|crm|hrm|prj|ana|integration)[_.]|^import (mdm|erp|crm|hrm|prj|ana|integration)[_.]" backend/ 2>/dev/null || true); \
	 if [ -n "$$bad" ]; then echo "✗ 铁律六违规，import 了其他组件仓库：$$bad"; exit 1; fi; \
	 bad2=$$(grep -rnE "^(from|import) infra\.[a-z_]+" backend/ 2>/dev/null | grep -v "infra\.print" || true); \
	 if [ -n "$$bad2" ]; then echo "✗ 铁律六违规，import 了 infra 域下的其他组件：$$bad2"; exit 1; fi; \
	 echo "✓ 无组件间 import"

module-check:  ## 铁律七：模块能被合进外壳（§12.5、§13.3 铁律七）
	@# ⚠️ 只扫 backend/app 下除 migrate.py 外的文件（app/migrate.py 是"装配"，
	@# 同 Go 版 cmd/migrate 的既有先例，允许读 os.environ）。
	@grep -qE 'async def create_module\(rt: Runtime\) -> Module' backend/app/module.py \
	   || { echo "✗ create_module 签名与阶段三 Task 1 后续补记不一致"; exit 1; }
	@bad=""; \
	 for f in $$(find backend/app -name '*.py' ! -name 'migrate.py'); do \
	   hit="$$(grep -nE 'os\.environ|os\.getenv' "$$f")"; \
	   [ -n "$$hit" ] && bad="$$bad$$f: $$hit\n"; \
	 done; \
	 if [ -n "$$bad" ]; then \
	   echo "✗ 模块代码里读了进程环境变量（22 个模块会互相顶掉，不报错）："; printf '%b' "$$bad"; exit 1; fi
	@bad=""; \
	 for f in $$(find backend/app -name '*.py' ! -name 'migrate.py'); do \
	   hit="$$(grep -nE 'sys\.exit|os\._exit|signal\.signal|set_tracer_provider|set_meter_provider|logging\.basicConfig|FastAPI\(\)' "$$f")"; \
	   [ -n "$$hit" ] && bad="$$bad$$f: $$hit\n"; \
	 done; \
	 if [ -n "$$bad" ]; then \
	   echo "✗ 模块碰了进程级的东西或自己装配（§12.5.2）："; printf '%b' "$$bad"; exit 1; fi
	@bad=$$(grep -rlE "flask|Flask|django|Django" backend/app pyproject.toml 2>/dev/null || true); \
	 if [ -n "$$bad" ]; then echo "✗ 用了 §12.4 禁掉的框架：$$bad"; exit 1; fi
	@echo "✓ 铁律七：入口签名对、零 os.environ、零进程级 init、栈合规"

docs-check:  ## 四份文档结构检查（总纲 §4 SOP-D）
	@bash ../../../infra/scripts/docs-check.sh infra-print

smoke:  ## 原则一：只装这一个组件就能起来（§1.5、§3.11 第 8 条）
	@(cd ../../.. && brickkit up --dry-run >/dev/null) && echo "✓ smoke（完整版见 make tier0）"

##@ 本地开发数据（总纲 SOP-W-7，仅本地/演示用，不进部署/CI）
seed:  ## 上传 2 个真实可渲染的示例模板（PDF 送货单 + ZPL 发货标签），幂等（重跑追加新版本，不重复建行）。链式建好身份，单独跑就能拿到完整数据
	@$(MAKE) -C ../iam-casdoor seed
	@$(MAKE) -C ../authz seed
	@bash scripts/seed.sh
