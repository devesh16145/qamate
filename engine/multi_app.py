"""Opt-in typed multi-app replay foundation; not yet an agent authoring surface."""
import re
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from playwright.sync_api import expect


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("An HTTP(S) URL without embedded credentials is required")
    return parsed.scheme, parsed.hostname.lower(), parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)


def navigation_guard(allowed):
    def guard(route):
        if route.request.is_navigation_request():
            try:
                permitted = origin(route.request.url) == allowed
            except ValueError:
                permitted = False
            if not permitted:
                route.abort()
                return
        route.continue_()
    return guard


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Binding(StrictModel):
    app: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    url: str

    @model_validator(mode="after")
    def valid_url(self):
        origin(self.url)
        return self

    @property
    def key(self):
        return self.app, self.actor


class Step(StrictModel):
    app: str
    actor: str
    op: Literal["open", "click", "fill", "capture", "expect_text"]
    test_id: str | None = None
    # A captured ID scopes the locator to a row with data-record-id, without
    # interpolating untrusted text into CSS or executable code.
    record: str | None = None
    value: str | None = None
    capture: str | None = None
    timeout_ms: int = Field(default=5000, ge=1, le=30000)

    @model_validator(mode="after")
    def valid_shape(self):
        if self.op == "open":
            if any(x is not None for x in (self.test_id, self.record, self.value, self.capture)):
                raise ValueError("open uses only the registered binding URL")
        elif not self.test_id or not self.test_id.strip():
            raise ValueError("A nonempty test_id is required")
        if (self.op in {"fill", "expect_text"}) != (self.value is not None):
            raise ValueError("value is required only for fill/expect_text")
        if self.op == "expect_text" and not self.value.strip():
            raise ValueError("Empty assertions are not evidence")
        if (self.op == "capture") != (self.capture is not None):
            raise ValueError("capture name is required only for capture")
        if self.capture is not None and not self.capture.strip():
            raise ValueError("Empty capture name")
        return self


class Workflow(StrictModel):
    bindings: list[Binding] = Field(min_length=1, max_length=20)
    steps: list[Step] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_graph(self):
        keys = {b.key for b in self.bindings}
        if len(keys) != len(self.bindings):
            raise ValueError("Duplicate app/actor binding")
        apps = {}
        for binding in self.bindings:
            if binding.app in apps and apps[binding.app] != origin(binding.url):
                raise ValueError("An app cannot silently change origins between actors")
            apps[binding.app] = origin(binding.url)
        captures, opened = set(), set()
        for step in self.steps:
            key = (step.app, step.actor)
            if key not in keys:
                raise ValueError("Unregistered app/actor")
            if step.op == "open":
                opened.add(key)
            elif key not in opened:
                raise ValueError("Open the binding before interacting")
            if step.record is not None and step.record not in captures:
                raise ValueError("Undefined captured record")
            if step.capture is not None:
                if step.capture in captures:
                    raise ValueError("Duplicate capture name")
                captures.add(step.capture)
        return self


class MultiAppReplay:
    """Owns isolated contexts. No implicit credential or storage-state sharing.

    Each run gets fresh contexts and captures. Navigation outside a binding's
    exact origin is blocked (SSO allowlists are deliberately not implemented).
    Call close or use the context manager, including after a failed replay.
    """

    def __init__(self, browser):
        self.browser = browser
        self.contexts = {}
        self.pages = {}
        self.captures = {}

    def close(self):
        try:
            for context in self.contexts.values():
                context.close()
        finally:
            self.contexts.clear()
            self.pages.clear()
            self.captures.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def run(self, workflow: Workflow | dict):
        # Validate even an existing model, whose nested lists may have mutated.
        workflow = Workflow.model_validate(workflow.model_dump() if isinstance(workflow, Workflow) else workflow)
        self.close()
        bindings = {b.key: b for b in workflow.bindings}
        completed = []
        for index, step in enumerate(workflow.steps):
            self.execute_step(bindings[(step.app, step.actor)], step)
            completed.append({"step": index + 1, "app": step.app, "actor": step.actor, "op": step.op})
        return {"status": "passed", "completed": completed}

    def execute_step(self, binding: Binding, step: Step):
        """Shared execution primitive for replay and grounded live recording."""
        if binding.key != (step.app, step.actor):
            raise ValueError("Step binding mismatch")
        self._execute_bound_step(binding, step)

    def context_options(self, binding):
        """Host-owned context setup; generated workflows never carry auth material."""
        return {}

    def _execute_bound_step(self, binding, step):
        # Kept in one path so live recording cannot heal differently to replay.
        key = step.app, step.actor
        if key not in self.pages:
            context = self.browser.new_context(service_workers="block", **self.context_options(binding))
            self.contexts[key] = context
            context.route("**/*", navigation_guard(origin(binding.url)))
            self.pages[key] = context.new_page()
        page = self.pages[key]
        page.set_default_timeout(step.timeout_ms)
        if step.op == "open":
            page.goto(binding.url, wait_until="domcontentloaded", timeout=step.timeout_ms)
        else:
            if origin(page.url) != origin(binding.url):
                raise ValueError("Active page escaped its app binding")
            locator = page.get_by_test_id(step.test_id)
            if step.record is not None:
                record_id = self.captures[step.record]["value"]
                escaped = page.evaluate("value => CSS.escape(value)", record_id)
                locator = page.locator('[data-record-id=' + escaped + ']').get_by_test_id(step.test_id)
            if step.op == "click":
                locator.click()
            elif step.op == "fill":
                locator.fill(step.value)
            elif step.op == "capture":
                expect(locator).to_have_count(1, timeout=step.timeout_ms)
                expect(locator).to_have_text(re.compile(r"\S"), timeout=step.timeout_ms)
                value = locator.inner_text().strip()
                if not value or len(value) > 512:
                    raise ValueError("Captured record ID must be nonempty and bounded")
                self.captures[step.capture] = {"value": value, "app": step.app, "actor": step.actor}
            elif step.op == "expect_text":
                expect(locator).to_have_text(step.value, timeout=step.timeout_ms)
        if origin(page.url) != origin(binding.url):
            raise ValueError("Active page escaped its app binding")
