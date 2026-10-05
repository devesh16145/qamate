"""Recorded steps: one definition that both RENDERS to test code and EXECUTES live.

A step is a small dict, e.g.
    {"op": "fill", "target": 'page.get_by_role("textbox", name="Username")',
     "name": "Username", "value": "standard_user", "data_key": "username"}

`call_spec(step)` turns it into a `Flow` method call (method + arguments). `render()`
prints that call as Python for the generated test; `execute()` performs the identical
call on a live page. Because both come from the same spec, the generated test is
exactly the sequence of calls that ran while authoring.

Targets and expected texts may refer to the test's data (`tc_data["company_name"]`):
`parameterize()` rewrites a recorded step so that a value typed earlier is read from the
data wherever it shows up again -- in the name of the record's link, in the text a check
expects. A test then follows its data (and its per-run `{unique}` values) instead of
repeating literals that only held for the authoring run.
"""
import json
import re
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


ACTIONS = {"goto", "click", "dblclick", "hover", "fill", "type", "select", "check", "press", "upload", "close_tab"}
CHECKS = {"expect_url", "expect_visible", "expect_hidden", "expect_text", "expect_value",
          "expect_checked", "expect_page_text", "expect_count"}
OPS = ACTIONS | CHECKS


def _value(step):
    if step.get("data_key"):
        return Data(step["data_key"])
    if step.get("value_expr"):            # a text built from test data: "City: " + tc_data["city"]
        return Expr(step["value_expr"])
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


def _dialog(step, text=True):
    kw = {}
    if step.get("dialog") in ("accept", "dismiss"):
        kw["dialog"] = step["dialog"]
        if text and step.get("dialog_text") is not None:
            kw["dialog_text"] = step["dialog_text"]
    return kw


def _timeout(step):
    return {"timeout": step["timeout"]} if step.get("timeout") else {}


def call_spec(step):
    """(method, positional args, keyword args) for one step."""
    op = step["op"]
    if op not in OPS:
        raise ValueError(f"unknown step op {op!r}")
    target = Expr(step["target"]) if step.get("target") else None
    name = step.get("name")
    if op == "goto":
        return "goto", [step["value"], name], {}
    if op == "close_tab":
        return "close_tab", [name], {}
    if op == "click":
        return op, [target, name], {**_effects(step), **_dialog(step), **({"new_tab": True} if step.get("new_tab") else {})}
    if op == "dblclick":
        return op, [target, name], {**_effects(step), **_dialog(step)}
    if op == "hover":
        kw = {"expect_visible": Expr(step["expect_visible"])} if step.get("expect_visible") else {}
        return "hover", [target, name], kw
    if op in ("fill", "type"):
        return op, [target, _value(step), name], {}
    if op == "select":
        return "select", [target, _value(step), name], _dialog(step, text=False)
    if op == "check":
        return "check", [target, bool(step.get("checked", True)), name], _dialog(step, text=False)
    if op == "press":
        return "press", [step["value"], target, name], {**_effects(step), **_dialog(step)}
    if op == "upload":
        return "upload", [target, Code("TEST_UPLOAD_IMAGE") if not step.get("value") else step["value"], name], {}
    if op == "expect_url":
        return "expect_url", [step["value"], name], _timeout(step)
    if op in ("expect_visible", "expect_hidden"):
        return op, [target, name], _timeout(step)
    if op == "expect_text":
        return "expect_text", [target, _value(step), name], {**({"exact": True} if step.get("exact") else {}),
                                                               **({"present": False} if step.get("present") is False else {}),
                                                               **_timeout(step)}
    if op == "expect_value":
        return "expect_value", [target, _value(step), name], _timeout(step)
    if op == "expect_checked":
        return "expect_checked", [target, bool(step.get("checked", True)), name], _timeout(step)
    if op == "expect_page_text":
        return "expect_page_text", [_value(step), bool(step.get("present", True)), name], _timeout(step)
    if op == "expect_count":
        return "expect_count", [target, int(step["count"]), name], _timeout(step)
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
        return eval(arg.code, {"page": page, "tc_data": data})
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


# ── data-driven steps ────────────────────────────────────────────────────────
_STRING = re.compile(r'"(?:[^"\\\\]|\\\\.)*"')


def _data_expr(text, literals):
    """A Python expression for `text` that reads the data values it contains from tc_data:
    'Vendor QA-7731 saved' with {"code": "QA-7731"} -> '"Vendor " + tc_data["code"] + " saved"'.
    None if it contains none. A value only counts as a whole word or phrase."""
    for key, value in literals:
        match = re.search(r"(?<![^\W_])" + re.escape(value) + r"(?![^\W_])", text) if value else None
        if match:
            before, after = text[:match.start()], text[match.end():]
            parts = [_data_expr(before, literals) or json.dumps(before, ensure_ascii=False)] if before else []
            parts.append(f"tc_data[{json.dumps(key)}]")
            if after:
                parts.append(_data_expr(after, literals) or json.dumps(after, ensure_ascii=False))
            return " + ".join(parts)
    return None


def usable_literals(live_data, always=()):
    """Data values worth following through later steps, longest first: distinctive text
    (six or more characters, not just a number), plus any value named in `always` (per-run
    unique values). Short common words ("Pune", "Test", "12") stay literals, so a locator
    is never tied to a value it merely happens to contain."""
    out = [(k, str(v)) for k, v in live_data.items()
           if isinstance(v, str) and v and (k in always or (len(v) >= 6 and re.search(r"[^\W\d_]", v)))]
    return sorted(out, key=lambda kv: -len(kv[1]))


def parameterize(step, literals):
    """Rewrite one recorded step in place so it reads typed values from the test data."""
    if not literals:
        return step
    if step.get("target") and step["op"] not in ("fill", "type"):
        def swap(match):
            try:
                text = json.loads(match.group(0))
            except ValueError:
                return match.group(0)
            return _data_expr(text, literals) or match.group(0)
        step["target"] = _STRING.sub(swap, step["target"])
    if step["op"] in ("expect_text", "expect_page_text", "expect_value") and not step.get("data_key") \
            and isinstance(step.get("value"), str):
        whole = next((k for k, v in literals if v == step["value"]), None)
        if whole:
            step["data_key"] = whole
        else:
            expr = _data_expr(step["value"], literals)
            if expr:
                step["value_expr"] = expr
    return step
