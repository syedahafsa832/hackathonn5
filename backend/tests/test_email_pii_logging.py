"""Regression guard: customer/Workspace email data must not be written to logs."""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROTECTED_MODULES = (
    "src/workers/message_processor.py",
    "src/workers/brand_message_processor.py",
    "src/channels/email_poller.py",
    "src/services/email_filter_service.py",
    "src/services/email_guardian_service.py",
    "src/services/brand_gmail_service.py",
    "src/services/intent_detector.py",
)

UNSAFE_NAMES = {
    "customer_email", "sender", "subject", "content", "reply_body",
    "gmail_msg_id", "gmail_message_id", "message_ids",
    "thread_id", "email", "structured", "data",
}


class UnsafeLogVisitor(ast.NodeVisitor):
    def __init__(self, relative):
        self.relative = relative
        self.violations = []

    def visit_Call(self, node):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
            and func.attr in {"debug", "info", "warning", "error", "exception"}
        ):
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):
                    for part in arg.values:
                        if isinstance(part, ast.FormattedValue):
                            names = {n.id for n in ast.walk(part.value) if isinstance(n, ast.Name)}
                            if names.intersection(UNSAFE_NAMES):
                                self.violations.append(
                                    f"{self.relative}:{node.lineno} logs variables {sorted(names.intersection(UNSAFE_NAMES))}"
                                )
                            rendered = ast.unparse(part.value)
                            if "gmail_email" in rendered or "gmail_message_id" in rendered:
                                self.violations.append(f"{self.relative}:{node.lineno} logs Gmail identifiers")
        self.generic_visit(node)


def test_email_processing_logs_do_not_interpolate_customer_content_or_identifiers():
    violations = []
    for relative in PROTECTED_MODULES:
        source = (BACKEND / relative).read_text(encoding="utf-8")
        visitor = UnsafeLogVisitor(relative)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)
    assert not violations, "\\n".join(violations)
