import pytest
from public_bench_tasks import TASKS, required_outcome_groups
from public_outcome_audit import audit_source


def source_for(name):
    lines = ['def test_' + TASKS[name]['tc_id'].replace('-', '_') + '():']
    for index, group in enumerate(required_outcome_groups(name)):
        lines.append(f"    page.get_by_role('button', name='transition {index}').click()")
        for outcome in group['outcomes']:
            kind, value = outcome['kind'], repr(outcome['value'])
            if kind == 'url_contains':
                statement = f"expect(page).to_have_url({value})"
            elif kind == 'element_has_value':
                statement = f"expect(page.get_by_label('field')).to_have_value({value})"
            else:
                method = 'to_contain_text' if kind == 'page_contains_text' else 'not_to_contain_text'
                statement = f"expect(page.locator('body')).{method}({value})"
            lines.append('    ' + statement)
    return lines


@pytest.mark.parametrize('name', ['ops-transfer', 'ops-validation', 'ops-allocation', 'sort-detail'])
def test_every_required_outcome_and_order_is_mandatory(name):
    lines = source_for(name)
    assert audit_source('\n'.join(lines), TASKS[name])['passed']
    for index, line in enumerate(lines):
        if 'expect(' in line:
            assert not audit_source('\n'.join(lines[:index] + lines[index+1:]), TASKS[name])['passed']
    # Combining repeated saved/reopened states into one state cannot prove persistence.
    collapsed = [line for line in lines if "name='transition" not in line]
    assert not audit_source('\n'.join(collapsed), TASKS[name])['passed']


def test_numeric_typing_and_blur_do_not_supply_readback_evidence():
    lines = source_for('ops-transfer')
    lines[1:1] = ["    page.get_by_role('spinbutton', name='Units').press('ControlOrMeta+a')",
                  "    page.get_by_role('spinbutton', name='Units').press_sequentially('12', delay=80)",
                  "    page.keyboard.press('Tab')"]
    assert audit_source('\n'.join(lines), TASKS['ops-transfer'])['passed']
    missing = [line for line in lines if ".to_have_value('12')" not in line]
    assert not audit_source('\n'.join(missing), TASKS['ops-transfer'])['passed']
    unsupported = [line.replace("press('Tab')", "press('Enter')") for line in lines]
    assert not audit_source('\n'.join(unsupported), TASKS['ops-transfer'])['passed']
