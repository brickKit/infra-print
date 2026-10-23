"""A view model as erp/billing formats it (its print.go viewModel), n lines."""


def invoice(n: int = 3) -> dict:
    lines = [{"no": i + 1, "code": f"P-{i:04d}", "name": f"螺丝 M6×{20 + i} Café Niño Größe €", "quantity": "12",
              "unit": "EA", "unit_price": "1,234.50", "net": "14,814.00", "tax_rate": "13%", "tax": "1,925.82"}
             for i in range(n)]
    return {"invoice": {"number": "INV-SH-2026-000123", "date": "2026年10月23日", "currency": "CNY"},
            "seller": {"name": "上海示例有限公司", "code": "SH"},
            "buyer": {"name": "北京客户股份有限公司", "tax_id": "91310000MA1FL0XX0X"},
            "lines": lines,
            "totals": {"net": "44,442.00", "tax": "5,777.46", "gross": "50,219.46",
                       "gross_in_words": "人民币伍万零贰佰壹拾玖元肆角陆分"}}
