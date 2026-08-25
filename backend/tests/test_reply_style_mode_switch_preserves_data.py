"""
Reply Style preset/learned mode switching must never lose learned data.

Root cause of the reported UI confusion: PATCH /reply-style already only
ever touches reply_style_mode/reply_style_preset/reply_style_learn_
automatically/reply_style_use_uploaded_only — it never wrote to
reply_style_profile or the reply_style_examples table. The learned profile
and uploaded examples were always preserved at the data layer; only the
Settings page's copy made it look like selecting a preset implied Learned
Style was still (or newly) active. These tests lock in the data-layer
guarantee the UI fix now correctly reflects: Premium -> Learned -> Premium
never deletes or overwrites the learned profile or uploaded examples.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_brands  # noqa: E402
from src.api.middleware.tenant_auth import get_current_tenant, TenantContext  # noqa: E402

app = FastAPI()
app.include_router(v2_brands.router, prefix="/api/v2")
client = TestClient(app)

BRAND_ID = "brand-1"
TENANT_ID = "tenant-1"

LEARNED_PROFILE = {
    "tone": "casual", "greeting_style": "informal", "closing_style": "brief",
    "emoji_usage": "rarely", "sentence_length": "short", "paragraph_style": "one paragraph",
    "use_bullets": False, "use_customer_name": "when natural",
}


def _override_tenant():
    async def _dep():
        return TenantContext(tenant_id=TENANT_ID, email="merchant@example.com")
    return _dep


def _with_tenant(fn):
    app.dependency_overrides[get_current_tenant] = _override_tenant()
    try:
        return fn()
    finally:
        app.dependency_overrides.clear()


def _brand(**overrides):
    b = {
        "id": BRAND_ID, "tenant_id": TENANT_ID,
        "reply_style_mode": "learned", "reply_style_preset": None,
        "reply_style_profile": LEARNED_PROFILE,
    }
    b.update(overrides)
    return b


# 1. Selecting Premium marks Premium as the active mode.
def test_selecting_preset_sets_active_mode_and_preset():
    brand = _brand()
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.api.routes.v2_brands.supabase_update", return_value={**brand, "reply_style_mode": "preset", "reply_style_preset": "premium_luxury"}) as mock_update:
        resp = _with_tenant(lambda: client.patch(
            f"/api/v2/brands/{BRAND_ID}/reply-style", json={"mode": "preset", "preset": "premium_luxury"}
        ))
    assert resp.status_code == 200, resp.text
    updates = mock_update.call_args.args[2]
    assert updates["reply_style_mode"] == "preset"
    assert updates["reply_style_preset"] == "premium_luxury"


# 2. Selecting Premium does not delete the learned profile.
def test_selecting_preset_never_writes_to_the_learned_profile_field():
    brand = _brand()
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.api.routes.v2_brands.supabase_update", return_value=brand) as mock_update:
        resp = _with_tenant(lambda: client.patch(
            f"/api/v2/brands/{BRAND_ID}/reply-style", json={"mode": "preset", "preset": "premium_luxury"}
        ))
    assert resp.status_code == 200, resp.text
    updates = mock_update.call_args.args[2]
    assert "reply_style_profile" not in updates


# 3. Selecting Premium does not delete uploaded examples.
def test_selecting_preset_never_touches_the_examples_table():
    brand = _brand()
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.api.routes.v2_brands.supabase_update", return_value=brand) as mock_update, \
         patch("src.api.routes.v2_brands.supabase_delete") as mock_delete:
        resp = _with_tenant(lambda: client.patch(
            f"/api/v2/brands/{BRAND_ID}/reply-style", json={"mode": "preset", "preset": "premium_luxury"}
        ))
    assert resp.status_code == 200, resp.text
    mock_delete.assert_not_called()
    for call in mock_update.call_args_list:
        assert call.args[0] != "reply_style_examples"


# 4. Switching back to Learned still works after a preset was selected.
def test_switch_back_to_learned_still_works_after_selecting_a_preset():
    # Brand currently on 'preset' mode, but its learned profile from before
    # is still intact (never deleted by the earlier preset switch).
    brand = _brand(reply_style_mode="preset", reply_style_preset="premium_luxury", reply_style_profile=LEARNED_PROFILE)
    # switch_reply_style_to_learned delegates to reply_style_service.switch_to_learned(),
    # which reads/writes via its own supabase_select/supabase_update import —
    # a separate reference from v2_brands', so both must be patched.
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.services.reply_style_service.supabase_select", return_value=[brand]), \
         patch("src.services.reply_style_service.supabase_update", return_value={**brand, "reply_style_mode": "learned"}) as mock_update:
        resp = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/reply-style/switch-to-learned"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    updates = mock_update.call_args.args[2]
    assert updates["reply_style_mode"] == "learned"


def test_switch_to_learned_fails_safe_if_profile_was_genuinely_never_generated():
    """Regression guard: the mode switch itself still requires a real
    profile to exist — this endpoint doesn't fabricate one."""
    brand = _brand(reply_style_mode="preset", reply_style_preset="premium_luxury", reply_style_profile=None)
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.services.reply_style_service.supabase_select", return_value=[brand]):
        resp = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/reply-style/switch-to-learned"))
    assert resp.status_code == 400


# Round trip: Learned -> Premium -> Learned never loses the profile, since
# nothing in either code path ever clears reply_style_profile.
def test_learned_to_premium_to_learned_round_trip_preserves_profile():
    brand = _brand(reply_style_mode="learned")

    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand]), \
         patch("src.api.routes.v2_brands.supabase_update", return_value=brand) as mock_update:
        resp1 = _with_tenant(lambda: client.patch(
            f"/api/v2/brands/{BRAND_ID}/reply-style", json={"mode": "preset", "preset": "premium_luxury"}
        ))
    assert resp1.status_code == 200
    assert "reply_style_profile" not in mock_update.call_args.args[2]

    brand_after_preset = _brand(reply_style_mode="preset", reply_style_preset="premium_luxury")
    with patch("src.api.routes.v2_brands.supabase_select", return_value=[brand_after_preset]), \
         patch("src.services.reply_style_service.supabase_select", return_value=[brand_after_preset]), \
         patch("src.services.reply_style_service.supabase_update", return_value={**brand_after_preset, "reply_style_mode": "learned"}) as mock_update2:
        resp2 = _with_tenant(lambda: client.post(f"/api/v2/brands/{BRAND_ID}/reply-style/switch-to-learned"))
    assert resp2.status_code == 200
    assert mock_update2.call_args.args[2]["reply_style_mode"] == "learned"
