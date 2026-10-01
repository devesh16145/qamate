import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_unseen_catalogue import audit, QUERY


def source(rows):
    return 'def test_TC_HOLDOUT_001(page, tc_data, base_url):\n' + '\n'.join('    ' + row for row in rows)


def rows():
    collection = 'expect(page.locator(\'a[href^="#/products/"]:visible:not([href$="/create"]):not([href$="/new"]):not([href$="/import"])\').first).to_be_visible()'
    return [f'expect(page.get_by_role("textbox", name="Search")).to_have_value({QUERY!r})',
        'expect(page.locator("body")).to_contain_text("No results found")',
        'page.get_by_role("button", name="Clear search").click()',
        'expect(page.get_by_role("textbox", name="Search")).to_have_value("")', collection,
        'page.get_by_role("link", name="Orders").click()',
        'expect(page).to_have_url(urljoin(base_url, "/#/commands"))',
        'page.get_by_role("link", name="Products").click()',
        'expect(page).to_have_url(urljoin(base_url, "/#/products"))', collection]


def test_predeclared_holdout_audit_rejects_missing_outcomes_and_wrong_field():
    required = rows()
    assert audit(source(required))
    for index in (0, 1, 3, 4, 6, 8, 9):
        assert not audit(source(required[:index] + required[index + 1:]))
    wrong = rows()
    wrong[3] = wrong[3].replace('Search', 'Unrelated')
    assert not audit(source(wrong))
    wrong = rows()
    wrong.insert(1, 'page.get_by_role("button", name="Clear search").click()')
    assert not audit(source(wrong))
