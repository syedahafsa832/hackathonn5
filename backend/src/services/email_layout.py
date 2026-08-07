"""
Outbound Email HTML Layout
===========================
Wraps a plain-text reply body (copy untouched, from AI generation or a
manual reply) in a lightly-styled HTML layout for the multipart/alternative
message brand_gmail_service sends alongside the existing plain-text part.

Layout only: no color accents, logo, or branding are introduced here - just
spacing (container padding, paragraph rhythm, sign-off separation) so the
HTML part reads with more breathing room than raw plain text. Table-based
with inline styles for compatibility with Outlook and other clients that
don't reliably support <style> blocks or modern CSS.
"""
import html as _html
from typing import List

_FONT_STACK = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def _split_blocks(body: str) -> List[str]:
    """Blank-line-delimited paragraphs; a single newline inside a block
    becomes a <br> so multi-line blocks (e.g. a sign-off) keep their breaks."""
    raw_blocks = body.replace("\r\n", "\n").split("\n\n")
    return [b.strip("\n") for b in raw_blocks if b.strip()]


def render_plain_text_email_html(body: str) -> str:
    """Render a plain-text email body as a spacious HTML email. Copy is
    escaped and reproduced verbatim - only spacing/structure is added."""
    blocks = _split_blocks(body or "")
    paragraphs_html = []
    for i, block in enumerate(blocks):
        escaped = _html.escape(block).replace("\n", "<br>")
        is_sign_off = i == len(blocks) - 1 and len(blocks) > 1
        # Extra top margin sets the final block (almost always the sign-off)
        # apart from the message body above it.
        margin = "32px 0 0 0" if is_sign_off else "0 0 24px 0"
        paragraphs_html.append(
            f'<p style="margin:{margin};padding:0;font-size:15px;line-height:1.7;color:#2b2b2b;">{escaped}</p>'
        )
    content = "".join(paragraphs_html)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title></title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f5;">
<tr>
<td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:8px;">
<tr>
<td style="padding:48px 40px;font-family:{_FONT_STACK};">
{content}
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>"""
