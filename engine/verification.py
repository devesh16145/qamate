"""Fail-closed verification of the actual requested test's JUnit results."""
import re
import xml.etree.ElementTree as ET


def verified_junit(path, tc_id):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    pattern = re.compile(r"^test_" + re.escape(tc_id.replace("-", "_")) + r"(?:_|\[|$)")
    cases = [case for case in root.iter("testcase") if pattern.match(case.get("name", ""))]
    return bool(cases) and all(not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")) for case in cases)
