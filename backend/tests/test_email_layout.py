"""
Outbound emails were plain MIMEText with no HTML layout at all, so "Checking
your store..." style spacing feedback couldn't be applied to anything.
render_plain_text_email_html() wraps the exact same copy in a spaced-out
HTML layout used as the multipart/alternative HTML part; these tests confirm
the original text survives unchanged (just escaped/structured) and that the
container/paragraph/sign-off spacing rules are actually present.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.email_layout import render_plain_text_email_html  # noqa: E402


def test_copy_is_preserved_verbatim():
    body = "Dear Syeda Hafsa, thank you for reaching out.\n\nwith care,\nLuna"
    html = render_plain_text_email_html(body)
    assert "Dear Syeda Hafsa, thank you for reaching out." in html
    assert "with care," in html
    assert "Luna" in html


def test_html_special_characters_are_escaped():
    body = "Order total: $50 & free shipping <3\n\nThanks"
    html = render_plain_text_email_html(body)
    assert "&amp;" in html
    assert "&lt;3" in html
    assert "<script" not in html.lower()


def test_container_has_generous_padding():
    html = render_plain_text_email_html("Hi there.\n\nBest,\nLuna")
    assert "padding:48px 40px" in html
    assert "max-width:600px" in html


def test_sign_off_block_gets_extra_top_margin():
    body = "Main message body here.\n\nwith care,\nLuna"
    html = render_plain_text_email_html(body)
    assert "margin:32px 0 0 0" in html  # sign-off block
    assert "margin:0 0 24px 0" in html  # regular paragraph spacing


def test_single_block_body_has_no_sign_off_margin():
    html = render_plain_text_email_html("Just one paragraph, no sign-off.")
    assert "margin:32px 0 0 0" not in html


def test_is_responsive_and_table_based():
    html = render_plain_text_email_html("Hello")
    assert 'name="viewport"' in html
    assert "<table" in html
