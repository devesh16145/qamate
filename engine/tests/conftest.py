"""Pytest bootstrap for the engine unit suite.

These tests exercise the PURE / file-backed logic of the agent engine (locator
selection, assertion lint, history trimming, suite CRUD, verdict parsing,
attachment handling, path containment). They never launch a browser or call an
LLM, so they live OUTSIDE `tests/` (whose conftest has autouse, browser-bound
fixtures) and import the engine modules as top-level modules.
"""

import os
import sys

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
