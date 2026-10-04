"""Recorded steps: one definition that both RENDERS to test code and EXECUTES live.

A step is a small dict, e.g.
    {"op": "fill", "target": 'page.get_by_role("textbox", name="Username")',
     "name": "Username", "value": "standard_user", "data_key": "username"}

`call_spec(step)` turns it into a `Flow` method call (method + arguments). `render()`
prints that call as Python for the generated test; `execute()` performs the identical
call on a live page. Because both come from the same spec, the generated test is
exactly the sequence of calls that ran while authoring.
"""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Expr:
    """Python code evaluated with `page` in scope (a locator expression)."""
    code: str


@dataclass(frozen=True)
class Data:
    """A value read from the test's data file (tc_data[key])."""
    key: str


@dataclass(frozen=True)
class Code:
    """A name available in the generated test module (e.g. TEST_UPLOAD_IMAGE)."""
    name: str


ACTIONS = {"goto", "click", "dblclick", "hover", "fill", "select", "check", "press", "upload"}
CHECKS = {"expect_url", "expect_visible", "expect_hidden", "expect_text", "expect_value",
          "expect_checked", "expect_page_text", "expect_count"}
OPS = ACTIONS | CHECKS


def _value(step):
    if step.get("data_key"):
        return Data(step["data_key"])
    return step.get("value")


def _effects(step):
    kw = {}
    if step.get("expect_url"):
        kw["expect_url"] = step["expect_url"]
    if step.get("expect_visible"):
        kw["expect_visible"] = Expr(step["expect_visible"])
    if step.get("expect_hidden"):
        kw["expect_hidden"] = Expr(step["expect_hidden"])
    return kw


def call_spec(step):
    """(method, positional args, keyword args) for one step."""
    op = step["op"]
    if op not in OPS:
        raise ValueError(f"unknown step op {op!r}")
    target = Expr(step["target"]) if step.get("target") else None
    name = step.get("name")
    if op == "goto":
        return "goto", [step["value"], name], {}
    if op in ("click", "dblclick"):
        return op, [target, name], _effects(step)
    if op == "hover":
        kw = {"expect_visible": Expr(step["expect_visible"])} if step.get("expect_visible") else {}
        return "hover", [target, name], kw
    if op == "fill":
        return "fill", [target, _value(step), name], {}
    if op == "select":
        return "select", [target, _value(step), name], {}
    if op == "check":
        return "check", [target, bool(step.get("checked", True)), name], {}
    if op == "press":
        return "press", [step["value"], target, name], _effects(step)
    if op == "upload":
        return "upload", [target, Code("TEST_UPLOAD_IMAGE") if not step.get("value") else step["value"], name], {}
    if op == "expect_url":
        return "expect_url", [step["value"], name], {}
    if op in ("expect_visible", "expect_hidden"):
        return op, [target, name], {}
    if op == "expect_text":
        return "expect_text", [target, _value(step), name], ({"exact": True} if step.get("exact") else {})
    if op == "expect_value":
        return "expect_value", [target, _value(step), name], {}
    if op == "expect_checked":
        return "expect_checked", [target, bool(step.get("checked", True)), name], {}
    if op == "expect_page_text":
        return "expect_page_text", [_value(step), bool(step.get("present", True)), name], {}
    if op == "expect_count":
        return "expect_count", [target, int(step["count"]), name], {}
    raise ValueError(op)


def _render_arg(arg):
    if isinstance(arg, Expr):
        return arg.code
    if isinstance(arg, Data):
        return f"tc_data[{json.dumps(arg.key)}]"
    if isinstance(arg, Code):
        return arg.name
    if isinstance(arg, str):
        return json.dumps(arg, ensure_ascii=False)
    return repr(arg)


def render(step):
    """The generated-test line for a step, e.g. 'flow.click(page.get_by_role(...), "Login")'."""
    method, args, kwargs = call_spec(step)
    while args and args[-1] is None:     # drop trailing optional arguments
        args = args[:-1]
    parts = [_render_arg(a) for a in args]
    parts += [f"{k}={_render_arg(v)}" for k, v in kwargs.items()]
    return f"flow.{method}({', '.join(parts)})"


def _execute_arg(arg, page, data, names):
    if isinstance(arg, Expr):
        return eval(arg.code, {"page": page})
    if isinstance(arg, Data):
        return data[arg.key]
    if isinstance(arg, Code):
        return names[arg.name]
    return arg


def execute(flow, step, data=None, names=None):
    """Perform the step through `flow` exactly as the generated test line would."""
    method, args, kwargs = call_spec(step)
    page = flow.page
    data, names = data or {}, names or {}
    call_args = [_execute_arg(a, page, data, names) for a in args]
    call_kwargs = {k: _execute_arg(v, page, data, names) for k, v in kwargs.items()}
    return getattr(flow, method)(*call_args, **call_kwargs)
