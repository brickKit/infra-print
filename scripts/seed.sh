#!/usr/bin/env bash
# 本组件自己的种子数据（总纲 SOP-W-7，从零设计）：种两个真实可用的
# 打印模板——`Render`/`BatchGetTemplates`/`ListTemplates` 建仓库以来
# 一直没有任何一条模板数据（不是 migration 播种，也没有任何组件的种子
# 脚本给它上传过），意味着这个组件此前完全没法被真机体验/演示——
# `Render` 拿一个不存在的 `template_id` 只会 404。
#
# ⚠️ `PUT /infra/print/templates/{id}`/`POST .../preview` 是真实的人类
# 管理操作（模板版本管理本来就是这个组件的核心功能之一），不是像
# `infra-workflow.CreateTask` 那样明确禁止绕过的组件间协议——本脚本走
# 真实 REST 接口，不是绕开设计边界。
#
# 两个模板覆盖两条渲染路径（设计计划 §3.2/§3.3）：
#   ① seed-template-delivery-note（channel=PDF，WeasyPrint+Jinja2，
#      真实按行循环渲染商品明细，同 Task 10 真机验证过的"带中文的 A4
#      送货单"效果）
#   ② seed-template-shipping-label（channel=ZPL，纯字符串模板替换，
#      含一个真实条码指令 ^BCN）
#
# 模板内容存在同目录 templates/ 下（.jinja2 后缀只是给人看这是模板，
# 不是真的用 jinja2 命令行工具处理——渲染由 infra-print 自己的
# Jinja2 环境在 Render 时才做），用 python3 json.dumps 组 body 避免手写
#转义 HTML/ZPL 里的引号和大括号出错。
#
# ⚠️ 本脚本没有配套 seed-clean.sh：print_templates 走版本管理设计
# （put_template 是 upsert-with-history，见 docs/手册.md"写入顺序不能
# 颠倒"那条），重跑本脚本只会给同一个模板 id 追加一个新版本，不会
# 重复建行——这本身就是幂等的，且新版本历史正是这个组件想要展示的
# 功能之一，不需要额外撤销机制。
#
# ⚠️ 前置条件（本脚本自己不建，靠 Makefile 的 seed 目标链式调用）：
#   - infra-iam-casdoor/infra-authz 的种子身份（dev.superuser 全权限）
#
# 用法：make -C components/infra/print seed
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$DIR/../../.." && pwd)"

C_GRN=$'\033[32m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
ok()  { echo "${C_GRN}✓${C_OFF} $*"; }
die() { echo "${C_RED}✗${C_OFF} $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }
need curl; need python3

CASDOOR_URL="${CASDOOR_URL:-http://localhost:8000}"
IAM_URL="${IAM_URL:-http://localhost:8200}"
PRINT_REST="${PRINT_REST:-http://localhost:8400}"
SEED_USER="dev.superuser"
SEED_PASSWORD="DevSeed123!"
SEED_APP="local-dev-seed-app"
COOKIE_JAR="$(mktemp)"

curl -sf -o /dev/null "$PRINT_REST/healthz" || die "infra-print（$PRINT_REST）连不上，先 brickkit up"

echo "   等 18 秒，让本组件的权限 bundle 轮询到最新授权……"
sleep 18

curl -c "$COOKIE_JAR" -s -o /dev/null -X POST "$CASDOOR_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"application":"app-built-in","organization":"built-in","username":"admin","password":"123","autoSignin":true,"type":"login"}'
APP_JSON="$(curl -b "$COOKIE_JAR" -s "$CASDOOR_URL/api/get-application?id=admin/$SEED_APP")"
CLIENT_ID="$(echo "$APP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin, strict=False)["data"]["clientId"])')"
CLIENT_SECRET="$(echo "$APP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin, strict=False)["data"]["clientSecret"])')"

ID_TOKEN="$(curl -s -X POST "$CASDOOR_URL/api/login/oauth/access_token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "username=$SEED_USER" \
  --data-urlencode "password=$SEED_PASSWORD" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=openid profile email" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id_token"])')"
[ -n "$ID_TOKEN" ] || die "拿不到 Casdoor id_token"

ACCESS_TOKEN="$(curl -s -X POST "$IAM_URL/api/iam/token" \
  -H "Content-Type: application/json" \
  -d "{\"casdoor_id_token\": \"$ID_TOKEN\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
[ -n "$ACCESS_TOKEN" ] || die "换应用 JWT 失败"
ok "已换到真实应用 JWT"

BODY_FILE="$(mktemp)"
trap 'rm -f "$COOKIE_JAR" "$BODY_FILE"' EXIT

put_template() { # id name channel content_file
  python3 -c '
import json, sys
name, channel, content_file = sys.argv[1], sys.argv[2], sys.argv[3]
with open(content_file, encoding="utf-8") as f:
    content = f.read()
print(json.dumps({"name": name, "channel": channel, "content": content, "enabled": True}))
' "$2" "$3" "$4" > "$BODY_FILE"
  curl -s -X PUT "$PRINT_REST/infra/print/templates/$1" \
    -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
    --data-binary @"$BODY_FILE" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"] + "(v" + str(d["version"]) + ")")'
}

echo "── 上传 2 个真实可渲染的模板（PDF 送货单 + ZPL 发货标签）──"
T1="$(put_template seed-template-delivery-note "「本地测试」送货单" PDF "$DIR/scripts/templates/delivery_note.html.jinja2")"
T2="$(put_template seed-template-shipping-label "「本地测试」发货标签" ZPL "$DIR/scripts/templates/shipping_label.zpl.jinja2")"
ok "模板：$T1 $T2"

echo "── 真实调一次 Render，确认两条渲染路径（WeasyPrint/纯文本替换）都真的跑得通 ──"
SAMPLE_DATA='{"order_no":"SO-SEED-0001","customer_name":"「本地测试」华南电子科技有限公司","order_date":"2026-09-12","package_count":"3","items":[{"name":"「本地测试」标准螺栓 M8","qty":"100","unit_price":"0.50","subtotal":"50.00"},{"name":"「本地测试」工业润滑油 20L","qty":"5","unit_price":"120.00","subtotal":"600.00"}],"total_amount":"650.00"}'
PDF_BYTES="$(curl -s -X POST "$PRINT_REST/infra/print/render" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d "{\"template_id\":\"seed-template-delivery-note\",\"data\":$SAMPLE_DATA}" | wc -c)"
[ "$PDF_BYTES" -gt 1000 ] || die "PDF 渲染结果异常小（$PDF_BYTES 字节），可能没渲染成功"
ZPL_OUTPUT="$(curl -s -X POST "$PRINT_REST/infra/print/render" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d "{\"template_id\":\"seed-template-shipping-label\",\"data\":$SAMPLE_DATA}")"
echo "$ZPL_OUTPUT" | grep -q "SO-SEED-0001" || die "ZPL 渲染结果里没有找到订单号，可能没渲染成功"
ok "真机渲染验证通过：PDF $PDF_BYTES 字节，ZPL 输出含正确的订单号变量替换"
