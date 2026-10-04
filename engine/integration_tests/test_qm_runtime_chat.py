"""The agent runtime's chat turn on the fast engine: a real session (browser, project,
persistence), with a scripted planner standing in for the model."""
import asyncio
import functools
import http.server
import json
import os
import sys
import threading

import pytest

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_chat
import agent_sessions
import project_store
import qm_agent
from test_qm_agent import ScriptedPlanner


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    # Projects store base URLs with a trailing slash, so serve the app at the site root.
    site = tmp_path_factory.mktemp("site")
    (site / "index.html").write_text(open(os.path.join(ENGINE, "fixtures", "operations.html"), encoding="utf-8").read(), encoding="utf-8")

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


def test_chat_turn_plans_runs_verifies_and_saves_into_the_project(server, tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({"llm": {"schema": 2, "default_provider": "local",
        "providers": {"local": {"preset": "ollama", "model": "fixture"}}}}), encoding="utf-8")
    project = project_store.create_project(str(root), "Dispatch", server)
    events = []
    monkeypatch.setattr(agent_chat, "emit", events.append)
    monkeypatch.setenv("ATS_ROOT", str(root))
    scripted = ScriptedPlanner([
        {"steps": [{"do": "click", "target": "New transfer"},
                   {"do": "fill", "target": "Transfer name", "value": "Batch 11"},
                   {"do": "select", "target": "Region", "value": "North"},
                   {"do": "select", "target": "Destination", "value": "Jaipur"},
                   {"do": "fill", "target": "Units", "value": "4"},
                   {"do": "click", "target": "Save transfer"}],
         "test": {"flow": "transfers", "title": "Create a transfer to Jaipur"}},
        {"steps": [{"do": "expect_text", "value": "Destination: Jaipur"}], "done": True},
    ])
    monkeypatch.setattr(qm_agent, "Planner", lambda *a, **k: scripted)

    async def scenario():
        rt = agent_chat.AgentRuntime()
        await rt.init({"project_id": project["id"], "headed": False})
        assert rt.engine == "fast"
        await rt.chat({"message": "Create a transfer Batch 11 to Jaipur with 4 units and check it"})
        await rt.shutdown()
        return rt

    rt = asyncio.run(scenario())
    done = [e for e in events if e.get("event") == "turn_complete"]
    assert done and "Saved **TC-TRANSFERS-001**" in done[-1]["text"], [e for e in events if e.get("event") in ("tool_result", "qm_step", "error", "log")][:8]
    assert done[-1]["fast_result"]["replay"]["ok"]
    test_file = root / "projects" / project["id"] / "tests" / "flows" / "transfers" / "test_transfers.py"
    assert test_file.exists() and 'flow.select(page.get_by_role("combobox", name="Destination"), "Jaipur"' in test_file.read_text()
    bubbles = agent_sessions.load_transcript(str(root), project["id"], rt.session_id)
    assert [b["role"] for b in bubbles[-2:]] == ["user", "assistant"]
    assert sum(1 for e in events if e.get("event") == "tool_call" and e.get("tool") == "step") == 7
