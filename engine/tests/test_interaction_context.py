from interaction_context import attach_interaction_context, decision_page


def test_unknown_probe_does_not_claim_clear_page():
    class Page:
        def evaluate(self, code):
            raise RuntimeError("closed")
    result = attach_interaction_context(Page(), {"elements": [{"ref": "x"}]}, {})
    assert result["interaction_context"] == {"status": "unknown"}
    assert "center_receives_pointer" not in result["elements"][0]


def test_decider_receives_error_context_but_not_unrelated_payload():
    observed = {"url": "https://fixture.test", "elements": [], "validation_errors": [{"text": "required"}],
                "interaction_context": {"visible_modal_count": 0}, "issues": ["unrelated diagnostic"]}
    result = decision_page(observed)
    assert result["validation_errors"] == observed["validation_errors"]
    assert result["interaction_context"] == observed["interaction_context"]
    assert "issues" not in result
