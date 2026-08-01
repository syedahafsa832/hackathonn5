"""
Quarantine "raw HTML in preview" bug (item 7 of the bug-fix pass).

email_guardian_service._create_quarantine_record() used to store
body_preview as (email.body)[:500] — a blunt truncation of the RAW email
body, which for HTML/marketing-style emails means the literal
<!DOCTYPE html>, <style> blocks, and Outlook VML conditional comments landed
in the quarantine list's preview text. _html_to_preview_text() should reduce
that down to clean, readable plain text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.email_guardian_service import _html_to_preview_text  # noqa: E402


OUTLOOK_STYLE_EMAIL = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<style type="text/css">
body { font-family: Arial, sans-serif; color: #333; }
.button { background: #06B6D4; padding: 10px; }
</style>
<!--[if mso]>
<v:shape id="shape1" type="#_x0000_t75">
<v:imagedata src="logo.png" />
</v:shape>
<![endif]-->
</head>
<body>
<table role="presentation" width="600">
<tr><td>
<p>Hi there,</p>
<p>We noticed you haven't logged in for a while. Come back and check out our new arrivals!</p>
<script>trackOpen('abc123');</script>
</td></tr>
</table>
</body>
</html>"""


def test_strips_doctype_style_and_vml_comments():
    preview = _html_to_preview_text(OUTLOOK_STYLE_EMAIL)

    assert "<!DOCTYPE" not in preview
    assert "<style" not in preview
    assert "font-family" not in preview
    assert "v:shape" not in preview
    assert "[if mso]" not in preview
    assert "trackOpen" not in preview
    assert "<table" not in preview
    assert "Hi there" in preview
    assert "new arrivals" in preview


def test_truncates_to_200_chars_by_default():
    long_text = "word " * 100  # 500 chars
    preview = _html_to_preview_text(long_text)
    assert len(preview) <= 200


def test_plain_text_input_passes_through_unchanged_aside_from_whitespace():
    assert _html_to_preview_text("Just a normal plain-text email body.") == "Just a normal plain-text email body."


def test_empty_input_returns_empty_string():
    assert _html_to_preview_text("") == ""
    assert _html_to_preview_text(None) == ""


def test_malformed_markup_falls_back_to_tag_strip_not_raw_passthrough():
    preview = _html_to_preview_text("<div><p>Unclosed tags <span>here")
    assert "<div>" not in preview
    assert "<p>" not in preview
    assert "here" in preview
