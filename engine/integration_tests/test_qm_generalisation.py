"""The generalisation probes as a gate: common web patterns the fast engine must handle on
its own (engine/probes/patterns.html) and an app whose data lives on the server.
A pattern added to the probe is a pattern this suite then protects."""
import importlib.util
import os
import sys

import pytest

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = os.path.join(ENGINE, "probes")
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(PROBES, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    saved, sys.argv = sys.argv, [name]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


def test_every_common_web_pattern_is_handled():
    patterns = _load("run_patterns")
    failed = [f"{name}: {why}" for name, verdict, why in patterns.RESULTS if verdict != "PASS"]
    assert not failed, "\n".join(failed)
    assert len(patterns.RESULTS) >= 45


def test_tests_that_create_data_are_saved_only_when_they_can_run_again():
    backend = _load("run_backend")
    for row in backend.run(verbose=False):
        assert row["saved"] == row["expected"], (row["title"], row["replay"])
        if row["expected"]:
            # authoring + two verification runs each made their own record
            assert len(set(row["names"])) == 3 and row["replay"]["runs"] == [True, True], row
            assert any('tc_data["vendor_name"]' in line for line in row["code"])
        else:
            assert "unique" in row["summary"]      # the reply says what to ask for
