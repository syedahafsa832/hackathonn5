"""
Root cause: POST /brands/{id}/reply-style/examples only inserted the row —
it never triggered profile generation. The only automatic trigger,
regenerate_if_due(), only re-checks by NEW APPROVED REPLY volume once a
profile already exists (see its `has_profile` branch), so it never notices
a newly uploaded example either on the very first upload (no profile yet,
nothing calls generate_learned_profile until the merchant happens to reload
Settings) or on a later upload (profile already exists, due-by-volume only
counts approved replies). Result: an uploaded example could sit in the DB
indefinitely without ever reaching the live response-generation path.

Fix: add_reply_example now calls generate_learned_profile(brand_id,
force=False) itself, best-effort, right after inserting the example — the
exact existing pipeline, no new system, no mode auto-switch (becoming the
active style is still the merchant's explicit "Switch to Learned Style"
action, unchanged).
"""
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402
from src.services import reply_style_service  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
OTHER_BRAND_ID = "brand-2"
TENANT_ID = "tenant-1"


def _override_tenant(tenant_id=TENANT_ID):
    async def _dep():
        return TenantContext(tenant_id=tenant_id, email="merchant@example.com")
    return _dep


def _with_tenant(fn, tenant_id=TENANT_ID):
    app.dependency_overrides[get_current_tenant] = _override_tenant(tenant_id)
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _brand(brand_id=BRAND_ID, **overrides):
    b = {
        "id": brand_id, "tenant_id": TENANT_ID,
        "reply_style_use_uploaded_only": False, "reply_style_profile": None,
    }
    b.update(overrides)
    return b


def _fake_db(brand, tickets=None, examples=None):
    """Shared fake for both v2_brands.supabase_select (ownership check) and
    reply_style_service.supabase_select (generate_learned_profile's own
    reads) — a real request would hit the same Postgres rows through both
    call sites."""
    def fn(table, params=None):
        if table == "brands":
            return [brand]
        if table == "tickets":
            return tickets or []
        if table == "reply_style_examples":
            return examples or []
        return []
    return fn


PROFILE_JSON = json.dumps({
    "tone": "casual", "greeting_style": "informal", "closing_style": "brief",
    "emoji_usage": "rarely", "sentence_length": "short", "paragraph_style": "one paragraph",
    "use_bullets": False, "use_customer_name": "when natural",
    "reasoning": ["Detected an informal, friendly greeting habit"],
})


def _fake_llm(capture=None):
    def call(*args, messages=None, **kwargs):
        if capture is not None:
            capture["messages"] = messages
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content=PROFILE_JSON))]
        return (response, "label", "model", {})
    return call


def _post_example(content, brand, tickets=None, examples=None, capture=None):
    # generate_learned_profile re-reads reply_style_examples from the DB
    # *after* the insert already happened — the fake must reflect that
    # post-insert state (the newly added row included), the same as real
    # Postgres would for the very next SELECT in the same request.
    post_insert_examples = [{"id": "ex-new", "content": content}] + (examples or [])
    fake_select = _fake_db(brand, tickets=tickets, examples=post_insert_examples)
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_insert", return_value={"id": "ex-new", "content": content}), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake_select), \
         patch("src.services.reply_style_service.supabase_update") as mock_update, \
         patch("src.services.ai_provider_manager.ai_provider_manager",
               MagicMock(has_providers=True, create_chat_completion=AsyncMock(side_effect=_fake_llm(capture)))):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{brand['id']}/reply-style/examples", json={"content": content}
        ))
    return resp, mock_update


# 1. Uploaded example reaches the live response-generation path (via the
# profile it seeds) — first-ever example, no profile existed before.
def test_first_example_upload_immediately_generates_a_learned_profile():
    brand = _brand(reply_style_profile=None)
    capture = {}
    resp, mock_update = _post_example(
        "Customer: Hi\n\nLuna's reply: helloo how can we help you?",
        brand, tickets=[], examples=[], capture=capture,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # The example text actually reached the extraction context (the "learned
    # representation" pipeline), not just sat inert in the examples table.
    assert "helloo how can we help you" in capture["messages"][1]["content"]

    profile_updates = [c.args[2] for c in mock_update.call_args_list if "reply_style_profile" in c.args[2]]
    assert profile_updates, "generate_learned_profile should have persisted a profile"
    assert profile_updates[0]["reply_style_profile"]["tone"] == "casual"


# The same gap, but for the "already has a profile" case — this is the one
# regenerate_if_due's own due-by-volume check would have silently missed
# forever (it only re-triggers off NEW approved replies once has_profile).
def test_example_upload_regenerates_even_when_a_profile_already_exists():
    brand = _brand(reply_style_profile={"tone": "old stale tone"})
    capture = {}
    resp, mock_update = _post_example(
        "Customer: Hey, need help\n\nLuna's reply: helloo, happy to help!",
        brand, tickets=[], examples=[{"id": "ex-old", "content": "old example"}], capture=capture,
    )

    assert resp.status_code == 200, resp.text
    assert "helloo, happy to help" in capture["messages"][1]["content"]
    profile_updates = [c.args[2] for c in mock_update.call_args_list if "reply_style_profile" in c.args[2]]
    assert profile_updates, "a new example must re-trigger generation even when a profile already exists"


# 2/3. Example influences the live style representation without being
# copied verbatim — the abstracted profile is what reaches build_style_prompt_block.
def test_example_influences_profile_but_is_never_copied_verbatim():
    brand = _brand(reply_style_profile=None)
    _, mock_update = _post_example(
        "Customer: Hi\n\nLuna's reply: helloo how can we help you?",
        brand, tickets=[], examples=[],
    )
    profile_updates = [c.args[2] for c in mock_update.call_args_list if "reply_style_profile" in c.args[2]]
    profile = profile_updates[0]["reply_style_profile"]

    block = reply_style_service.build_style_prompt_block(profile)
    assert "helloo how can we help you" not in block.lower()
    assert "casual" in block  # the abstracted descriptor, not the raw wording


# 4. Real facts never leak from an example into the "learned" style —
# structurally guaranteed by the STYLE_PROFILE_KEYS whitelist, even if the
# model's response includes something fact-shaped.
def test_generated_profile_drops_non_style_fields_even_if_model_returns_them():
    brand = _brand(reply_style_profile=None)
    sneaky_json = json.dumps({
        "tone": "casual", "greeting_style": "informal", "closing_style": "brief",
        "emoji_usage": "rarely", "sentence_length": "short", "paragraph_style": "one paragraph",
        "use_bullets": False, "use_customer_name": "when natural",
        "refund_policy": "Refunds available within 90 days",  # not a style field — must be dropped
        "reasoning": ["Detected a casual tone"],
    })

    def sneaky_llm(*args, messages=None, **kwargs):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content=sneaky_json))]
        return (response, "label", "model", {})

    content = "Customer: Hi\n\nLuna's reply: helloo how can we help you?"
    fake_select = _fake_db(brand, tickets=[], examples=[{"id": "ex-new", "content": content}])
    with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_brands.supabase_insert", return_value={"id": "ex-new"}), \
         patch("src.services.reply_style_service.supabase_select", side_effect=fake_select), \
         patch("src.services.reply_style_service.supabase_update") as mock_update, \
         patch("src.services.ai_provider_manager.ai_provider_manager",
               MagicMock(has_providers=True, create_chat_completion=AsyncMock(side_effect=sneaky_llm))):
        resp = _with_tenant(lambda: client.post(
            f"/api/v2/brands/{BRAND_ID}/reply-style/examples",
            json={"content": content},
        ))

    assert resp.status_code == 200
    profile_updates = [c.args[2] for c in mock_update.call_args_list if "reply_style_profile" in c.args[2]]
    stored_profile = profile_updates[0]["reply_style_profile"]
    assert "refund_policy" not in stored_profile


# 5. use_uploaded_only is still respected by the new upload-time trigger.
def test_use_uploaded_only_still_excludes_approved_replies_on_upload_trigger():
    brand = _brand(reply_style_use_uploaded_only=True, reply_style_profile=None)
    tickets = [{"human_approved": True, "ai_reply": "REAL APPROVED REPLY TEXT", "updated_at": "2026-01-01"}] * 25
    capture = {}
    resp, _ = _post_example(
        "Customer: Hi\n\nLuna's reply: UPLOADED EXAMPLE TEXT",
        brand, tickets=tickets, examples=[], capture=capture,
    )
    assert resp.status_code == 200
    prompt = capture["messages"][1]["content"]
    assert "UPLOADED EXAMPLE TEXT" in prompt
    assert "REAL APPROVED REPLY TEXT" not in prompt


# 6. Brand isolation — uploading for Brand A never regenerates Brand B's profile.
def test_example_upload_only_regenerates_the_owning_brand():
    brand_a = _brand(brand_id=BRAND_ID, reply_style_profile=None)
    with patch("src.services.reply_style_service.generate_learned_profile", new=AsyncMock()) as mock_generate:
        fake_select = _fake_db(brand_a, tickets=[], examples=[])
        with patch("src.api.routes.v2_brands.supabase_select", side_effect=fake_select), \
             patch("src.api.routes.v2_brands.supabase_insert", return_value={"id": "ex-new"}):
            resp = _with_tenant(lambda: client.post(
                f"/api/v2/brands/{BRAND_ID}/reply-style/examples", json={"content": "hi -> helloo"}
            ))
    assert resp.status_code == 200
    mock_generate.assert_awaited_once_with(BRAND_ID, force=False)
    assert OTHER_BRAND_ID not in [c.args[0] for c in mock_generate.await_args_list]


# 7. The organic "X of 20 approved replies" counter is unaffected by this
# change — uploading an example must never inflate it.
def test_upload_trigger_never_changes_the_approved_reply_count():
    brand = _brand(reply_style_profile=None)
    _, mock_update = _post_example(
        "Customer: Hi\n\nLuna's reply: helloo how can we help you?",
        brand, tickets=[], examples=[],
    )
    for call in mock_update.call_args_list:
        assert "approved_reply_count" not in call.args[2]  # not a real column; sanity that nothing fakes it
    fake_select = _fake_db(brand, tickets=[])
    with patch("src.services.reply_style_service.supabase_select", side_effect=fake_select):
        assert reply_style_service.count_eligible_approved_replies(BRAND_ID) == 0
