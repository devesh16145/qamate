import pytest
from decision import safe_decision_error
from llm import LLMError, LLMNotConfigured


@pytest.mark.parametrize("status,category", [(401, "authentication"), (402, "quota"),
    (403, "permission"), (429, "rate_limit"), (503, "provider_http")])
def test_http_classification_does_not_leak_provider_body(status, category):
    error = LLMError("private prompt and secret token https://private.example", status_code=status)
    assert safe_decision_error(error) == {"error_category": category, "http_status": status}


@pytest.mark.parametrize("error,category", [
    (LLMError("Invalid decision probability distribution"), "invalid_probabilities"),
    (LLMError("Invalid decision response"), "invalid_response"),
    (LLMError("Decision returned a choice outside the candidate set"), "invalid_choice"),
    (LLMNotConfigured("private env name"), "not_configured"),
    (TimeoutError("private URL"), "timeout"),
    (LLMError("unknown provider body with secret"), "provider_error"),
    (RuntimeError("private runtime data"), "internal_error"),
])
def test_only_allowlisted_categories_escape(error, category):
    assert safe_decision_error(error) == {"error_category": category}
