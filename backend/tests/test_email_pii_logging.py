"""Regression guards for avoiding Google/customer email content in app logs.

The support pipeline still stores message content in ticket records for the
merchant-facing product. It must not additionally duplicate that content,
addresses, subjects, or Gmail identifiers into operational logs.
"""
from pathlib import Path
import re

BACKEND = Path(__file__).resolve().parents[1]
LOGGED_FILES = (
    "src/workers/message_processor.py",
    "src/channels/email_poller.py",
    "src/services/email_filter_service.py",
    "src/services/email_guardian_service.py",
    "src/services/brand_gmail_service.py",
)

# These expressions represent message/customer values, not safe booleans or
# counts (e.g. bool(reply_body), len(messages), or type(exc).__name__).
FORBIDDEN_LOG_INTERPOLATIONS = (
    r"\{\s*customer_email\s*\}",
    r"\{\s*sender\s*\}",
    r"\{\s*subject\s*\}",
    r"\{\s*content\s*(?:\[:|\})",
    r"\{\s*reply_body\s*\[",
    r"\{\s*email\s*\}",
    r"\{\s*gmail_msg_id\s*\}",
    r"\{\s*gmail_message_id\s*\}",
    r"\{\s*brand\.get\(['\"]gmail_email['\"]\)\s*\}",
    r"message_ids\s*=",
    r"sender\s*=\s*\{",
    r"to_email\s*=\s*\{",
)

LOG_CALL = re.compile(r"logger\.(?:debug|info|warning|error|exception)\s*\(")


def test_email_processing_logs_do_not_interpolate_customer_content_or_identifiers():
    violations = []
    for relative_path in LOGGED_FILES:
        source = (BACKEND / relative_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        for index, line in enumerate(lines):
            # Include nearby continuation lines for multi-line logger calls.
            if LOG_CALL.search(line):
                snippet = "\n".join(lines[index:index + 5])
                for pattern in FORBIDDEN_LOG_INTERPOLATIONS:
                    if re.search(pattern, snippet):
                        violations.append(
                            f"{relative_path}:{index + 1} contains unsafe log interpolation {pattern}"
                        )
    assert not violations, "\n".join(violations)
