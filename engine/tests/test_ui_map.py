"""
Unit tests for the UI-map crawler's pure logic: hash-route URL identity
(SPA screens must not collapse into one page) — the run3 root cause for
"map says combobox, DOM says button" is covered by live locator validation,
whose status vocabulary is asserted here.
"""

import agent_chat as ac


def test_hash_routes_are_distinct_pages():
    a = ac._map_norm_url("https://admin.test/#/oms/cart")
    b = ac._map_norm_url("https://admin.test/#/oms/po")
    assert a != b
    assert a == "https://admin.test#/oms/cart"


def test_hash_route_query_dropped():
    assert ac._map_norm_url("https://admin.test/#/oms/cart?filter=%7B%7D&page=42") == \
           "https://admin.test#/oms/cart"


def test_plain_anchor_still_collapses():
    assert ac._map_norm_url("https://site.test/docs#install") == \
           ac._map_norm_url("https://site.test/docs")


def test_path_urls_unchanged_semantics():
    assert ac._map_norm_url("https://site.test/catalog?page=2") == "https://site.test/catalog"
    assert ac._map_norm_url("https://site.test/catalog/") == "https://site.test/catalog"
    assert ac._map_norm_url("") == ""


def test_trailing_slash_hash_route_stable():
    assert ac._map_norm_url("https://a.test/#/orders/") == ac._map_norm_url("https://a.test/#/orders")
