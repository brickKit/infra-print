#!/usr/bin/env bash
# 本组件的种子数据：两个真实可渲染的模板，覆盖两条渲染路径。
#   sales.delivery_note   PDF 送货单（WeasyPrint + Jinja2，按行循环明细）。前端"打印送货单"
#                         按钮写死这个 id，数据形状与它一致：order_no、customer_name、
#                         items[].product_name / qty / unit_price / subtotal、total_amount，
#                         order_date 可选。灌两个版本（草稿 → 正式），版本列表与回滚有东西可看。
#   sales.shipping_label  ZPL 发货标签（纯文本替换，含 ^BCN 条码指令）。
#
# 写法：直接写演示库 brickkit_db（psql 走 be-postgres 容器），每个模板一个事务；
# 模板已存在就整条跳过，所以可以重复跑，也不会覆盖管理员在界面上改过的模板。
# 不走 REST 上传：PUT /templates/{id} 要真 token，而本组件可以在没有 authz / iam 的
# 项目里单独起来。灌完经 gRPC Render（组件间调用面，不验 token）真渲染一次，确认
# 镜像里的 WeasyPrint、中文字体与 ZPL 替换都能用。
#
# 只给本地开发 / 演示用，不出现在任何部署或 CI 流程里。
# 用法：make -C components/infra/print seed（要求本组件已经 brickkit up 起来）
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$DIR/../../.." && pwd)"
source "$ROOT/infra/scripts/lib/seed-net.sh"

need docker; need python3

seed_net_check

SCHEMA="$(awk -F'\t' '$1=="infra-print"{print $2}' "$ROOT/registry/schemas.tsv")"
[ -n "$SCHEMA" ] || die "registry/schemas.tsv 里找不到 infra-print 的 schema"
GRPC_PORT="$(awk -F'\t' '$2=="infra/print"{print $4}' "$ROOT/registry/ports.tsv")"
[ -n "$GRPC_PORT" ] || die "registry/ports.tsv 里找不到 infra/print 的 grpc 端口"
TPL="$DIR/scripts/templates"

# seed_template <id> <name> <channel> <内容文件…>：按顺序作为版本 1、2…写入，最后一个是当前版本。
# 生成的 SQL 用美元引号包模板原文（模板里有引号、大括号、反斜杠），引号标签不能出现在原文里。
seed_template() {
  python3 - "$SCHEMA" "$@" <<'PY' | psqlx -q
import sys
schema, tid, name, channel, *files = sys.argv[1:]
contents = [open(f, encoding="utf-8").read() for f in files]

def lit(s: str, tag: str = "seed") -> str:
    q = f"${tag}$"
    assert q not in s, f"模板原文里出现了引号标签 {q}"
    return f"{q}{s}{q}"

rows = ",\n".join(
    f"({lit(tid)}, {lit(name)}, {lit(channel)}, {lit(c, 'tpl')}, {i}, true)"
    for i, c in enumerate(contents, start=1)
)
last = len(contents)
print(f"""
BEGIN;
SET LOCAL search_path TO {schema};
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM print_templates WHERE id = {lit(tid)}) THEN
    RAISE NOTICE '模板 % 已存在，跳过', {lit(tid)};
    RETURN;
  END IF;
  INSERT INTO print_templates (id, name, channel, content, version, enabled)
    VALUES ({lit(tid)}, {lit(name)}, {lit(channel)}, {lit(contents[-1], 'tpl')}, {last}, true);
  INSERT INTO print_template_versions (template_id, name, channel, content, version, enabled)
    VALUES {rows};
END
$do$;
COMMIT;
""")
PY
}

echo "── infra-print：灌 2 个模板（已存在的跳过）──"
seed_template sales.delivery_note "「本地测试」送货单" PDF "$TPL/delivery_note.draft.html.jinja2" "$TPL/delivery_note.html.jinja2"
seed_template sales.shipping_label "「本地测试」发货标签" ZPL "$TPL/shipping_label.zpl.jinja2"
psqlx -tA -F ' ' -c "SELECT id, 'v' || version FROM $SCHEMA.print_templates WHERE id LIKE 'sales.%' ORDER BY id" \
  | sed 's/^/  /'
ok "模板就绪"

echo "── 经 gRPC Render 真渲染一次（PDF 与 ZPL 两条路径）──"
GRPCURL="docker run --rm -i --network $NET -v $DIR/contracts:/contracts:ro fullstorydev/grpcurl:latest"
TARGET="$(service_name infra/print):$GRPC_PORT"
SAMPLE='{"order_no":"SO-SEED-0001","customer_name":"「本地测试」华南电子科技有限公司","order_date":"2026-09-12","package_count":"3","items":[{"product_name":"「本地测试」标准螺栓 M8","qty":"100","unit_price":"0.50","subtotal":"50.00"},{"product_name":"「本地测试」工业润滑油 20L","qty":"5","unit_price":"120.00","subtotal":"600.00"}],"total_amount":"650.00"}'

render() { # <template_id> -> grpcurl 的 JSON 输出
  python3 -c 'import json,sys; print(json.dumps({"template_id": sys.argv[1], "data_json": sys.argv[2], "source_component": "seed"}))' "$1" "$SAMPLE" \
    | $GRPCURL -plaintext -import-path /contracts -proto infra/print/v1/print.proto -d @ "$TARGET" infra.print.v1.PrintService/Render
}

render sales.delivery_note | python3 -c '
import base64, json, sys
r = json.load(sys.stdin); pdf = base64.b64decode(r["content"])
assert r["contentType"] == "application/pdf" and pdf[:4] == b"%PDF" and len(pdf) > 1000, (r.get("contentType"), len(pdf))
print(f"  sales.delivery_note → application/pdf，{len(pdf)} 字节")' || die "送货单渲染结果不对"
render sales.shipping_label | python3 -c '
import base64, json, sys
r = json.load(sys.stdin); text = base64.b64decode(r["content"]).decode()
assert r["contentType"] == "text/plain" and "SO-SEED-0001" in text and "^BCN" in text, text
print("  sales.shipping_label → text/plain，订单号已替换进 ZPL")' || die "发货标签渲染结果不对"
ok "两条渲染路径都能用"
