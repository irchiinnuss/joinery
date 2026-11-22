"""Which errors mean a provider ran out of quota, and what the next agent is told."""

from __future__ import annotations

from pathlib import Path

import pytest

from joinery.fallback import (
    candidates,
    continue_text,
    is_quota,
    provider_error,
    setting,
    staged_lines,
)


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your usage limit. Try again at 4:12 PM.",
        "429 Too Many Requests",
        "RESOURCE_EXHAUSTED: Quota exceeded for quota metric",
        "Rate limit reached for requests",
        "Your credit balance is too low",
    ],
)
def test_quota_words_are_recognised(text: str) -> None:
    assert is_quota(text)


@pytest.mark.parametrize(
    "text", ["", "The scene could not be saved.", "Permission denied", "Unauthorized"]
)
def test_other_errors_are_not_quota(text: str) -> None:
    assert not is_quota(text)


@pytest.mark.parametrize(
    ("text", "kind", "reset"),
    [
        (
            "You've hit your usage limit. Try again at 4:12 PM.",
            "usage_limit",
            "4:12 PM",
        ),
        (
            "5-hour limit reached ∙ resets 3pm (Europe/Berlin)",
            "usage_limit",
            "3pm (Europe/Berlin)",
        ),
        (
            "Claude AI usage limit reached|1760000000",
            "usage_limit",
            "2025-10-09 08:53 UTC",
        ),
        (
            "You've hit your usage limit. Upgrade to Pro, or try again in 2 days 3 hours.",
            "usage_limit",
            "in 2 days 3 hours",
        ),
        ("429 Too Many Requests: Please retry in 41.3s", "rate_limit", "in 41.3s"),
        ("RESOURCE_EXHAUSTED: Quota exceeded for quota metric", "usage_limit", ""),
        ("API Error: 500 Internal server error", "provider", ""),
    ],
)
def test_provider_errors_say_which_limit_and_when_it_lifts(
    text: str, kind: str, reset: str
) -> None:
    got_kind, got_reset, line = provider_error(text)
    assert (got_kind, got_reset) == (kind, reset)
    lead = {
        "usage_limit": "Usage limit reached",
        "rate_limit": "Rate limit reached",
        "provider": "Provider error",
    }[kind]
    assert line.startswith(lead)
    if kind == "provider":
        assert line.endswith(text)
    else:
        # The limit is said once: the provider's restating words are left out.
        assert text not in line and line.lower().count("limit") == 1
    if reset:
        # Right after which limit: "Usage limit reached. It resets at 4:12 PM."
        assert line.index(reset) < len(lead) + 20


def test_a_limits_line_keeps_what_the_provider_adds() -> None:
    _, _, line = provider_error(
        "Your credit balance is too low to access the API. "
        "Please go to Plans & Billing to purchase credits."
    )
    assert line == (
        "Usage limit reached. Please go to Plans & Billing to purchase credits."
    )


def test_the_setting_names_a_known_agent_only(tmp_path: Path) -> None:
    assert setting(tmp_path) == ""
    toml = tmp_path / "joinery.toml"
    toml.write_text('[agents]\nfallback_provider = "claude"\n')
    assert setting(tmp_path) == "claude"
    toml.write_text('[agents]\nfallback_provider = "someone-else"\n')
    assert setting(tmp_path) == ""
    toml.write_text("[agents\n")
    assert setting(tmp_path) == ""


def test_candidates_leave_out_the_current_agent_and_the_signed_out() -> None:
    found = candidates("codex", lambda agent: agent in {"codex", "gemini"})
    assert [c["id"] for c in found] == ["gemini"]


def test_the_next_agent_hears_the_request_and_the_staged_files() -> None:
    changes = [
        {"path": "a.gd", "before": "", "after": "h1"},
        {"path": "b.tscn", "before": "h2", "after": "h3"},
        {"path": "c.png", "before": "h4", "after": ""},
    ]
    text = continue_text("Codex", " Make an ashtray ", "Earlier: a table.", changes)
    assert text.startswith("Codex ran out of quota")
    assert "The user's request: Make an ashtray" in text
    assert "- a.gd (new)" in text and "- b.tscn (changed)" in text
    assert "- c.png (removed)" in text and "Earlier: a table." in text
    assert staged_lines([]) == "Nothing is staged yet."
    many = staged_lines([{"path": f"f{i}", "after": "x"} for i in range(45)])
    assert many.endswith("and 5 more files")
