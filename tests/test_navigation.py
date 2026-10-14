from app.infrastructure.db.navigation import NAVIGATION_GROUPS, normalize_navigation_order


def test_navigation_order_keeps_requested_order_and_appends_new_pages():
    order = normalize_navigation_order({"全链扫描": ["fee_screen"]})

    assert order["全链扫描"] == ["fee_screen", "meteora_scanner"]
    assert order["策略与数据"] == NAVIGATION_GROUPS["策略与数据"]


def test_navigation_order_drops_unknown_and_duplicate_pages():
    order = normalize_navigation_order({
        "全链扫描": ["fee_screen", "missing", "fee_screen", "meteora_scanner"],
    })

    assert order["全链扫描"] == ["fee_screen", "meteora_scanner"]
    assert "链上代币" not in order
