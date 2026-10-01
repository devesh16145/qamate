"""
Unit tests for the page-normalization layer (engine/normalizer.py) and its
integration points: element_to_model's escaped-ID/synthetic-role handling and
the BrowserSession option-click step recording used by the select_option ladder.

These detectors are what keep "badly built website" failures away from the LLM
(IDs with spaces, Paid/Unpaid name collisions, hidden Tailwind inputs, MUI
number inputs, role-less clickable divs) — pure logic, no browser, no LLM.
"""

import pytest

from normalizer import escape_css_id, disambiguate, infer_hints, match_option
from app_explorer import element_to_model


def test_textarea_contents_do_not_replace_associated_label():
    model = element_to_model({"tag": "textarea", "label_text": "Description",
                              "text": "Qamate original description", "value": "Qamate original description"})
    assert model["name"] == "Description"
import agent_chat as ac


# ──────────────────────────────────────────────────────────────────────────────
# escape_css_id — '#Last 7 days' broke Playwright's CSS engine (run3 blocker)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("el_id,expected", [
    ("save-btn", "#save-btn"),
    ("_private", "#_private"),
    ("Last 7 days", "[id='Last 7 days']"),          # spaces -> attribute form
    (":r3:", "[id=':r3:']"),                         # MUI colon ids
    ("a.b", "[id='a.b']"),                           # dot would read as class
    ("9lives", "[id='9lives']"),                     # leading digit invalid in #
    ("it's", "[id='it\\'s']"),                       # quote escaped
    ("", ""),
    ("   ", ""),
])
def test_escape_css_id(el_id, expected):
    assert escape_css_id(el_id) == expected


# ──────────────────────────────────────────────────────────────────────────────
# disambiguate — name='Paid' must not match 'Unpaid'/'Paid 0' (run3 blocker)
# ──────────────────────────────────────────────────────────────────────────────

def _btn(name):
    return {"primary": {"by": "role", "role": "button", "name": name}, "fallbacks": []}


def test_disambiguate_sets_exact_on_substring_collision():
    paid, unpaid = _btn("Paid"), _btn("Unpaid")
    disambiguate([paid, unpaid])
    assert paid["primary"].get("exact") is True       # 'paid' is inside 'unpaid'
    assert "exact" not in unpaid["primary"]           # 'unpaid' is not inside 'paid'


def test_disambiguate_prefix_collision_both_directions():
    short, long_ = _btn("Save"), _btn("Save Changes")
    disambiguate([short, long_])
    assert short["primary"].get("exact") is True
    assert "exact" not in long_["primary"]


def test_disambiguate_identical_names_marked_ambiguous():
    a, b = _btn("Delete"), _btn("Delete")
    disambiguate([a, b])
    assert a.get("ambiguous") and b.get("ambiguous")
    assert "exact" not in a["primary"]                # exact can't fix identical names


def test_disambiguate_different_roles_untouched():
    btn, link = _btn("Orders"), {"primary": {"by": "role", "role": "link", "name": "Orders New"},
                                 "fallbacks": []}
    disambiguate([btn, link])
    assert "exact" not in btn["primary"]              # collision check is per-role


def test_disambiguate_fixes_role_fallbacks_too():
    m = {"primary": {"by": "test_id", "value": "paid-tab"},
         "fallbacks": [{"by": "role", "role": "button", "name": "Paid"}]}
    disambiguate([m, _btn("Unpaid")])
    assert m["fallbacks"][0].get("exact") is True


# ──────────────────────────────────────────────────────────────────────────────
# infer_hints — quirks become data, not playbook prose
# ──────────────────────────────────────────────────────────────────────────────

def test_hint_force_click_for_hidden_radio():
    m = {"visible": False, "input_type": "radio"}
    assert "force-click" in infer_hints(m)


def test_hint_sequential_fill_for_number_input():
    m = {"visible": True, "input_type": "number"}
    assert "sequential-fill" in infer_hints(m)


def test_no_hints_for_ordinary_button():
    m = {"visible": True, "input_type": ""}
    assert infer_hints(m) == []


def test_hidden_text_input_gets_no_force_hint():
    # force-click is only for radio/checkbox; a hidden text input stays plain
    m = {"visible": False, "input_type": "text"}
    assert "force-click" not in infer_hints(m)


# ──────────────────────────────────────────────────────────────────────────────
# match_option — decorated portal option labels (ACME CORP case)
# ──────────────────────────────────────────────────────────────────────────────

OPTIONS = [
    "ACME CORP LIMITED(9850763440)ACME CORP INDIA PVT LTD.",
    "Acme Traders",
    "Test Brand",
]


def test_match_exact_case_insensitive():
    assert match_option(OPTIONS, "acme traders") == "Acme Traders"


def test_match_value_inside_decorated_option():
    assert match_option(OPTIONS, "ACME CORP LIMITED") == OPTIONS[0]


def test_match_prefers_exact_over_contains():
    opts = ["Test Brand Premium", "Test Brand"]
    assert match_option(opts, "Test Brand") == "Test Brand"


def test_match_option_inside_value():
    assert match_option(["Acme"], "Acme Limited") == "Acme"


def test_match_token_overlap():
    assert match_option(["LIMITED ACME CORP co."], "acme corp limited") == "LIMITED ACME CORP co."


def test_match_none_when_unrelated():
    assert match_option(OPTIONS, "Zebra Corp") is None


def test_match_empty_inputs():
    assert match_option([], "x") is None
    assert match_option(OPTIONS, "") is None


# ──────────────────────────────────────────────────────────────────────────────
# element_to_model — escaped ids, synthetic clickable divs, rect passthrough
# ──────────────────────────────────────────────────────────────────────────────

def test_model_uses_attribute_form_for_unsafe_id():
    el = {"tag": "input", "type": "radio", "id": "Last 7 days", "text": "",
          "aria_label": "", "visible": True}
    m = element_to_model(el)
    strats = [m["primary"]] + m["fallbacks"]
    css = [s["value"] for s in strats if s.get("by") == "css"]
    assert "[id='Last 7 days']" in css
    assert not any(v.startswith("#Last") for v in css)


def test_model_synthetic_div_targets_by_text():
    el = {"tag": "div", "text": "COD", "synthetic": True, "visible": True,
          "rect": {"x": 10, "y": 20, "w": 80, "h": 30}}
    m = element_to_model(el)
    assert m["synthetic_role"] is True
    assert m["role"] == "button"                       # synthesized for the agent's benefit
    assert m["primary"] == {"by": "text", "value": "COD"}  # but located by TEXT, not role
    assert not any(s.get("by") == "role" for s in m["fallbacks"])
    assert m["rect"]["w"] == 80


def test_model_normal_element_unchanged_shape():
    el = {"tag": "button", "text": "Save", "visible": True}
    m = element_to_model(el)
    assert m["primary"] == {"by": "role", "role": "button", "name": "Save"}
    assert m["synthetic_role"] is False


def test_model_input_named_from_associated_label():
    el = {"tag": "input", "type": "text", "text": "", "aria_label": "",
          "label_text": "Customer Name", "visible": True}
    m = element_to_model(el)
    assert m["name"] == "Customer Name"


# ──────────────────────────────────────────────────────────────────────────────
# Live-run lessons (2026-06-10 smoke): empty-state/loading/destructive guards,
# placeholder-over-context naming, layout-scoped locators for nameless controls
# ──────────────────────────────────────────────────────────────────────────────

def test_empty_state_options_filtered():
    s = _bare_session()
    opts = ['No customers found for "Acme"Try a different search term',
            "Please wait...", "Loading...", "ACME CORP LIMITED(985)IND"]
    assert s._real_options(opts) == ["ACME CORP LIMITED(985)IND"]


def test_loading_re_subset_of_empty_state():
    s = _bare_session()
    assert s._LOADING_RE.search("Please wait...")
    assert s._LOADING_RE.search("Loading results")
    assert not s._LOADING_RE.search('No customers found for "x"')  # final, don't wait


def test_destructive_options_never_fuzzy_matched():
    s = _bare_session()
    assert s._DESTRUCTIVE_OPT_RE.search("Logout")
    assert s._DESTRUCTIVE_OPT_RE.search("Sign out")
    assert s._DESTRUCTIVE_OPT_RE.search("Delete address")
    assert not s._DESTRUCTIVE_OPT_RE.search("Sign In Options")


def test_model_placeholder_outranks_context_label():
    # The customer search field: nameless but placeholder'd. Placeholder IS the
    # accessible name — must not be displaced by a nearby heading.
    el = {"tag": "input", "type": "text", "placeholder": "Customer Number, Name, or Business Name",
          "context_label": "Customer & Shipping Information", "visible": True}
    m = element_to_model(el)
    assert m["name"].startswith("Customer Number")
    assert m["context_named"] is False
    assert m["primary"] == {"by": "role", "role": "textbox",
                            "name": "Customer Number, Name, or Business Name"}


def test_model_context_named_uses_layout_selector():
    # The shipping combobox: zero-width-space name, sibling label "Shipping Address".
    el = {"tag": "div", "role": "combobox", "text": "​",
          "context_label": "Shipping Address", "visible": True}
    m = element_to_model(el)
    assert m["context_named"] is True
    assert m["name"] == "Shipping Address"
    assert m["primary"]["by"] == "css"
    assert ':near(:text("Shipping Address")' in m["primary"]["value"]
    # role+name / text locators must NOT be generated (label is not the aria name)
    assert not any(s.get("by") == "text" for s in [m["primary"]] + m["fallbacks"])
    assert {"by": "role", "role": "combobox"} in m["fallbacks"]


def test_model_zero_width_name_cleaned():
    el = {"tag": "div", "role": "combobox", "text": "​‌", "visible": True}
    m = element_to_model(el)
    assert m["name"] == ""          # zwsp junk never becomes a name
    assert m["ref"] != ""


# ──────────────────────────────────────────────────────────────────────────────
# _record_option_click — ladder selections must land in the generated test
# ──────────────────────────────────────────────────────────────────────────────

def _bare_session():
    s = ac.BrowserSession.__new__(ac.BrowserSession)   # no browser start
    s.steps, s.assertions, s.step_id, s.input_counter = [], [], 0, 0
    return s


def test_record_option_click_role_option_rawline():
    s = _bare_session()
    s._record_option_click("ACME CORP LIMITED", "role-option")
    assert len(s.steps) == 1
    raw = s.steps[0]["rawLine"]
    assert raw == 'page.get_by_role("option").filter(has_text="ACME CORP LIMITED").first.click()'
    assert s.steps[0]["type"] == "click"


def test_record_option_click_text_fallback_rawline():
    s = _bare_session()
    s._record_option_click("Acme", "vision")
    assert s.steps[0]["rawLine"] == 'page.get_by_text("Acme").first.click()'


def test_record_option_click_escapes_quotes():
    s = _bare_session()
    s._record_option_click('Say "hi"', "role-option")
    raw = s.steps[0]["rawLine"]
    compile(raw, "<rawline>", "exec")                  # must be valid Python code


# -- compact_elements: grouping, cap, ctx/href exposure -----------------------

from normalizer import compact_elements


def _m(ref, role="button", name="", container="", visible=True, **kw):
    d = {"ref": ref, "role": role, "name": name, "container": container,
         "visible": visible, "tag": "button"}
    d.update(kw)
    return d


def test_compact_groups_identical_repeats():
    models = [_m(f"req-{i}", name="Request Product") for i in range(12)]
    items, hidden = compact_elements(models, limit=40)
    assert len(items) == 1 and hidden == 0
    assert items[0]["repeats"] == 12
    assert items[0]["ref"] == "req-0"          # act on the first instance's ref


def test_compact_grouping_saves_unique_elements_from_the_cap():
    # 50 repeated card buttons + the search box LAST: without grouping the cap
    # (40) would crowd the search box out — the exact BENCH-010 failure.
    models = [_m(f"card-{i}", name="Request Product") for i in range(50)]
    models.append(_m("search", role="searchbox", name="Search Product Name, SKU, Brand",
                     input_type="search"))
    items, _ = compact_elements(models, limit=40)
    names = [i["name"] for i in items]
    assert "Search Product Name, SKU, Brand" in names
    assert items[0]["repeats"] == 50


def test_compact_same_name_different_container_not_grouped():
    # sidebar tab vs card button: SAME name, different region -> both listed
    # with ctx so the agent can tell the intents apart (BENCH-006).
    models = [_m("tab-req", name="Request Product", container="left-rail"),
              _m("card-req", name="Request Product", container="")]
    items, _ = compact_elements(models, limit=40)
    assert len(items) == 2
    assert items[0]["ctx"] == "left-rail" and "ctx" not in items[1]


def test_compact_href_and_flags_passthrough():
    models = [_m("po-link", role="link", name="PO-442", href="#/orders/442"),
              _m("dis", name="Save", disabled=True),
              _m("amb", name="Paid", ambiguous=True)]
    items, _ = compact_elements(models, limit=40)
    assert items[0]["href"] == "#/orders/442"
    assert items[1]["disabled"] is True and items[2]["ambiguous"] is True


def test_compact_hidden_counted_not_listed_by_default():
    models = [_m("a", name="A"), _m("b", name="B", visible=False, hidden_reason="display:none")]
    items, hidden = compact_elements(models, limit=40)
    assert hidden == 1 and len(items) == 1
    items2, _ = compact_elements(models, limit=40, include_hidden=True)
    assert len(items2) == 2 and items2[1]["hidden_reason"] == "display:none"


def test_compact_nameless_elements_never_grouped():
    models = [_m("x1", name=""), _m("x2", name="")]
    items, _ = compact_elements(models, limit=40)
    assert len(items) == 2 and all("repeats" not in i for i in items)


def test_compact_cap_still_counts_repeats_of_listed_groups():
    models = ([_m(f"u-{i}", name=f"Unique {i}") for i in range(40)]
              + [_m("dup", name="Unique 0")])    # repeat of an already-listed entry
    items, _ = compact_elements(models, limit=40)
    assert len(items) == 40
    assert items[0].get("repeats") == 2


# -- match_index: identical-locator groups get DOM-order ordinals --------------

def _model(ref, by="role", role="button", name="", value=""):
    if by == "role":
        primary = {"by": "role", "role": role, "name": name}
    else:
        primary = {"by": "text", "value": value}
    return {"ref": ref, "role": role, "name": name or value, "visible": True,
            "tag": "button", "primary": primary, "fallbacks": []}


def test_disambiguate_assigns_match_index_to_identical_groups():
    models = [_model("inc-0", name="Incorrect?"), _model("inc-1", name="Incorrect?"),
              _model("inc-2", name="Incorrect?"), _model("save", name="Save")]
    disambiguate(models)
    assert [m.get("match_index") for m in models[:3]] == [0, 1, 2]
    assert "match_index" not in models[3]              # unique -> no ordinal
    assert all(m.get("ambiguous") for m in models[:3])


def test_disambiguate_match_index_for_text_primaries():
    models = [_model("a", by="text", value="Request Product"),
              _model("b", by="text", value="Request Product")]
    disambiguate(models)
    assert models[0]["match_index"] == 0 and models[1]["match_index"] == 1


def test_disambiguate_substring_groups_not_index_grouped():
    # 'Paid' vs 'Unpaid' is the exact=True case, NOT an identical group.
    models = [_model("p", name="Paid"), _model("u", name="Unpaid")]
    disambiguate(models)
    assert "match_index" not in models[0] and "match_index" not in models[1]
    assert models[0]["primary"].get("exact") is True


def test_record_strategy_flags_nth_for_grouped_elements():
    s = _bare_session()
    s.page = None                                      # _count -> -1 (uncountable)
    el = _model("inc-2", name="Incorrect?")
    el["match_index"] = 2
    strat, loc_str, warn, nth = s._record_strategy(el)
    # nth ordinal is returned for act() to refine against the LIVE locator
    # (geometry ground truth); loc_str stays bare at this stage.
    assert nth == 2 and ".nth(" not in loc_str and warn is None
    compile(f"{loc_str}.nth({nth}).click()", "<rawline>", "exec")  # act()-built line is valid code


def test_record_strategy_unique_element_unchanged():
    s = _bare_session()
    s.page = None
    strat, loc_str, warn, nth = s._record_strategy(_model("save", name="Save"))
    assert nth is None and ".nth(" not in loc_str


def test_compact_filters_framework_junk_names():
    models = [_m("junk", name='self.__next_f.push([1,"25:..'),
              _m("real", name="Request Product")]
    items, _ = compact_elements(models, limit=40)
    assert [i["name"] for i in items] == ["Request Product"]
