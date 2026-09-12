"""The guard is the part of this that has to be right even when the model is wrong.

Run: .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.guard import apply_guard, find_violations, looks_degenerate  # noqa: E402


def kinds(raw: str, cleaned: str) -> list[str]:
    return [v.kind for v in find_violations(raw, cleaned)]


class TestInventedContacts:
    def test_invented_email_is_refused(self):
        raw = "mail priya about the delay"
        cleaned, violations = apply_guard(raw, "Mail priya@company.com about the delay.")
        assert violations and violations[0]["kind"] == "email"
        assert "@" not in cleaned

    def test_invented_phone_is_refused(self):
        raw = "call nikhil and tell him we're late"
        assert kinds(raw, "Call Nikhil on 98765 43210 and tell him we're late.") == ["phone"]

    def test_invented_url_is_refused(self):
        raw = "send them the deck"
        assert kinds(raw, "Send them the deck at https://drive.google.com/deck.") == ["url"]


class TestSpokenContactsSurvive:
    def test_spoken_email_is_kept(self):
        raw = "send the deck to ananya at skan dot ai before the call"
        cleaned, violations = apply_guard(raw, "Send the deck to ananya@skan.ai before the call.")
        assert violations == []
        assert "ananya@skan.ai" in cleaned

    def test_spoken_digits_are_kept(self):
        raw = "call nine eight seven six five four three two one zero"
        assert kinds(raw, "Call 9876543210.") == []

    def test_digits_typed_in_the_raw_are_kept(self):
        raw = "the number is 9876543210"
        assert kinds(raw, "The number is 9876543210.") == []

    def test_spoken_url_is_kept(self):
        raw = "it's on github dot com slash shaurya"
        assert kinds(raw, "It's on https://github.com/shaurya.") == []


class TestDegenerate:
    def test_looping_output_falls_back_to_raw(self):
        raw = "the build is green"
        looped = "The build is green. " * 12
        cleaned, violations = apply_guard(raw, looped)
        assert violations and violations[0]["kind"] == "degenerate"
        assert cleaned == raw

    def test_normal_output_is_not_degenerate(self):
        assert not looks_degenerate("um uh like you know", "")
        assert not looks_degenerate("send it to rohan", "Send it to Rohan.")


class TestPassThrough:
    def test_clean_output_is_untouched(self):
        raw = "so um send it to rahul no wait rohan"
        cleaned, violations = apply_guard(raw, "Send it to Rohan.")
        assert violations == []
        assert cleaned == "Send it to Rohan."

    def test_filler_only_stays_empty(self):
        cleaned, violations = apply_guard("um uh like you know", "")
        assert cleaned == ""
        assert violations == []
