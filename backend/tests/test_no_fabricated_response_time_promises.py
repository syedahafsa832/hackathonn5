"""
Phase 6 (pre-Autopilot UX pass): when an action is pending human approval,
the customer-facing message must be truthful and never promise a specific
response time the product doesn't actually guarantee - no SLA/business-hours
config exists anywhere in this codebase (confirmed in
specs/007-autopilot-automation/research.md's customer-messaging section).

Found and fixed several instances of exactly this - "within 2 hours",
"within 24 hours", "shortly", "soon" - hardcoded into the LLM instructions in
return_actions_integration.py and into two fixed customer-facing strings
(customer_success_agent.py's fallback replies, v2_chat_widget.py's
human-takeover notice). This is a lint-style regression test: it greps the
actual source for the banned phrasing rather than exercising the full
message-generation pipeline, since the fix is entirely textual (no branching
logic changed) and a text scan catches a reintroduced fabricated promise
just as reliably, in every one of these code paths at once.

Deliberately NOT flagged: "within 24 hours"/"within X business days" language
describing real external system behavior (a shipping carrier's tracking
system, a bank's refund posting time) - those are honest facts about
downstream systems, not a promise about how fast tResolv's own team acts.
"""
import os
import re

_BACKEND_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

# Files known to construct customer-facing "your request is pending human
# approval" style messaging.
_FILES_TO_CHECK = [
    "services/return_actions_integration.py",
    "agent/customer_success_agent.py",
    "api/routes/v2_chat_widget.py",
]

_BANNED_PATTERNS = [
    re.compile(r"within \d+ hours?\b", re.IGNORECASE),
    re.compile(r"under \d+ hours?\b", re.IGNORECASE),
    re.compile(r"will (respond|reply|follow up|get back to you) shortly", re.IGNORECASE),
    re.compile(r"hear back (soon|shortly)", re.IGNORECASE),
    re.compile(r"get back to you shortly", re.IGNORECASE),
]

# Carrier/bank timing facts, not internal team SLA promises - not banned.
_ALLOWED_SUBSTRINGS = [
    "3–5 business days", "3-5 business days",  # bank refund posting time
    "within 24 hours of dispatch", "within 24 hours of shipping",  # carrier tracking activation
    "update within 24 hours",  # tracking status update cadence
]


def _read(relative_path: str) -> str:
    with open(os.path.join(_BACKEND_SRC, relative_path), "r", encoding="utf-8") as f:
        return f.read()


def test_no_fabricated_response_time_promises_in_customer_facing_messages():
    violations = []
    for relative_path in _FILES_TO_CHECK:
        content = _read(relative_path)
        for line_no, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if any(allowed in line for allowed in _ALLOWED_SUBSTRINGS):
                continue
            # A bare code comment (dev-facing example/rationale) can never
            # reach a customer, and "NEVER invent ..." is a prohibition
            # instruction to the model, not an instance of the thing it
            # prohibits - both are false positives for this scan, not real
            # customer-facing text.
            if stripped.startswith("#") or "NEVER invent" in line:
                continue
            for pattern in _BANNED_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{relative_path}:{line_no}: {line.strip()}")

    assert not violations, "Fabricated/vague response-time promise(s) found:\n" + "\n".join(violations)
