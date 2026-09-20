"""
Luna Sandbox tests: isolation, safety boundaries, the four canonical instant
demos, the "Ask Luna" AI budget, provider failure, and real-tenant protection.
"""
import ast
import asyncio
import json
import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import pytest  # noqa: E402

from src.services import sandbox_service as sb  # noqa: E402
from src.services import sandbox_data as sd  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_FILES = [
    os.path.join(BACKEND, "src", "services", "sandbox_data.py"),
    os.path.join(BACKEND, "src", "services", "sandbox_service.py"),
    os.path.join(BACKEND, "src", "api", "routes", "v2_sandbox.py"),
]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_budget():
    sb.sandbox_ai_budget = sb.SandboxAIBudget()
    yield


# ── 1. Data isolation / 12. no real tenant data exposed ───────────────────────

FORBIDDEN_IMPORT_FRAGMENTS = (
    "shopify_service", "brand_gmail_service", "actions_service", "actions_manager",
    "supabase_client", "supabase_service", "return_actions_integration",
    "customer_success_agent", "email_automation", "requests", "httpx", "smtplib",
)


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
            found += [f"{node.module}.{a.name}" for a in node.names]
    return found


def test_sandbox_modules_import_nothing_that_can_reach_real_systems():
    for path in SANDBOX_FILES:
        for imported in _imports(path):
            assert not any(bad in imported for bad in FORBIDDEN_IMPORT_FRAGMENTS), f"{path} imports {imported}"


def test_scenario_endpoints_take_no_tenant_store_or_brand_identifier():
    src = open(SANDBOX_FILES[2], encoding="utf-8").read()
    assert "brand_id" not in src and "store_id" not in src and "tenant_id=" not in src.split("async def ask(")[0]


def test_no_real_tenant_data_is_exposed_in_any_scenario():
    real_markers = ("tresolv", "gmail.com", "myshopify.com", "syedahafsa", "bushrazohaib")
    for s in sb.list_scenarios():
        blob = json.dumps(sb.build_scenario(s["id"])).lower()
        assert not any(m in blob for m in real_markers)
        assert '"sandbox": true' in blob


# ── 2/3. sandbox action & email cannot reach real Shopify/Gmail ─────────────────

def test_sandbox_actions_never_reach_shopify_or_gmail_or_http():
    from src.services import shopify_service, brand_gmail_service
    boom = MagicMock(side_effect=AssertionError("sandbox reached a real system"))
    with patch.object(shopify_service.ShopifyClient, "_request_sync", boom), \
         patch.object(shopify_service.ShopifyClient, "_request", new=AsyncMock(side_effect=AssertionError("shopify"))), \
         patch("requests.post", boom), patch("requests.get", boom), patch("requests.put", boom), patch("requests.delete", boom), \
         patch.object(brand_gmail_service.BrandGmailService, "send_email", new=AsyncMock(side_effect=AssertionError("gmail"))), \
         patch.object(brand_gmail_service.BrandGmailService, "_send_email_sync", boom), \
         patch.object(brand_gmail_service.BrandGmailService, "send_reply_in_thread", new=AsyncMock(side_effect=AssertionError("gmail"))):
        for scenario in ("cancel", "refund"):
            sb.build_scenario(scenario)
            assert sb.resolve_scenario(scenario, "approve")["ticket_status"] == "resolved"
            assert sb.resolve_scenario(scenario, "reject")["ticket_status"] == "needs_human"
    boom.assert_not_called()


def test_sandbox_approval_results_are_clearly_labelled_simulated_and_do_not_mutate_fixtures():
    before = json.dumps(sd.ORDERS, sort_keys=True)
    result = sb.resolve_scenario("cancel", "approve")
    assert result["sandbox"] is True
    assert "Sandbox action" in result["action_label"] and "No real store was changed" in result["action_label"]
    assert any("simulated" in s["detail"].lower() or "simulated" in s["label"].lower() for s in result["steps"])
    assert json.dumps(sd.ORDERS, sort_keys=True) == before  # fixtures never mutated
    assert sd.ORDERS["1048"]["cancelled_at"] is None


def test_bad_decision_and_actionless_scenarios_are_rejected():
    with pytest.raises(sb.SandboxError):
        sb.resolve_scenario("cancel", "delete_everything")
    with pytest.raises(sb.SandboxError):
        sb.resolve_scenario("returns", "approve")
    with pytest.raises(sb.SandboxError):
        sb.build_scenario("does-not-exist")


# ── 4-7. canonical demos ────────────────────────────────────────────────────────

def test_canonical_cancellation_demo_read_reason_action_approval_resolution():
    s = sb.build_scenario("cancel")
    assert s["ticket"]["messages"][0]["body"] == "hey, can you cancel order #1048? i ordered the wrong size"
    assert [x["key"] for x in s["steps"]] == ["understood", "order", "policy", "eligibility", "approval", "draft"]
    assert s["steps"][4]["status"] == "needs_approval"
    assert s["evidence"]["order"]["order_number"] == "1048"
    assert s["pending_action"]["type"] == "cancel_order" and s["pending_action"]["amount"] == 84.0
    assert s["requires_approval"] is True and "$84.00" in s["draft_reply"]
    approved = sb.resolve_scenario("cancel", "approve")
    assert approved["order_after"]["cancelled_at"] and approved["ticket_status"] == "resolved"
    assert "has been cancelled" in approved["final_reply"]
    rejected = sb.resolve_scenario("cancel", "reject")
    assert rejected["order_after"]["cancelled_at"] is None and rejected["final_reply"] is None


def test_canonical_refund_demo_uses_order_and_policy_and_requires_approval():
    s = sb.build_scenario("refund")
    assert s["pending_action"]["type"] == "refund" and s["pending_action"]["amount"] == 62.0
    assert "30-day" in s["steps"][3]["detail"] and "Eligible" in s["steps"][3]["detail"]
    assert s["evidence"]["policy"]["title"] == "Returns & Refunds Policy"
    approved = sb.resolve_scenario("refund", "approve")
    assert approved["order_after"]["financial_status"] == "refunded" and "$62.00" in approved["final_reply"]


def test_canonical_policy_demo_finds_the_right_source_and_needs_no_approval():
    s = sb.build_scenario("returns")
    assert s["evidence"]["source"]["title"] == "Returns & Refunds Policy"
    assert s["pending_action"] is None and s["requires_approval"] is False
    assert "30 days" in s["draft_reply"]


def test_canonical_product_demo_answers_from_sample_inventory():
    s = sb.build_scenario("product")
    assert "available in M" in s["draft_reply"] and "$96.00" in s["draft_reply"]
    sold_out = next(v for v in s["evidence"]["product"]["variants"] if v["size"] == "S")
    assert sold_out["inventory"] == 0
    assert s["pending_action"] is None


def test_eligibility_is_computed_from_fixtures_not_hardcoded():
    shipped = dict(sd.ORDERS["1048"], fulfillment_status="fulfilled")
    assert sb.evaluate_cancellation(shipped)["eligible"] is False
    old = dict(sd.ORDERS["1051"], delivered_at="2026-07-01T00:00:00+00:00")
    assert sb.evaluate_refund(old)["eligible"] is False
    final_sale = dict(sd.ORDERS["1051"], line_items=[dict(sd.ORDERS["1051"]["line_items"][0], final_sale=True)])
    assert sb.evaluate_refund(final_sale)["eligible"] is False


def test_instant_demos_make_zero_ai_calls_and_work_when_every_provider_is_down():
    boom = AsyncMock(side_effect=AssertionError("instant demo called the AI"))
    with patch.object(sb.ai_provider_manager, "create_chat_completion", boom):
        for s in sb.list_scenarios():
            assert sb.build_scenario(s["id"])["steps"]
    boom.assert_not_called()


# ── 8. AI rate limit ──────────────────────────────────────────────────────────────

def _ai_response(body):
    msg = MagicMock(); msg.content = body
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return (resp, "test", "model", {})


def test_ask_luna_success_is_grounded_call_and_decrements_budget():
    good = AsyncMock(return_value=_ai_response(json.dumps({"intent": "product_question", "reply_body": "Yes, Medium is in stock.\n- Luna"})))
    with patch.object(sb.ai_provider_manager, "create_chat_completion", good):
        out = _run(sb.ask_luna("Is the black maxi dress in medium?", "tenant-a", "1.1.1.1"))
    assert out["ok"] is True and out["remaining"] == sb.sandbox_ai_budget.per_tenant - 1
    sent = json.dumps(good.call_args.kwargs["messages"])
    assert "Northstar Apparel" in sent and "SAMPLE STORE DATA" in sent


def test_ask_luna_is_hard_limited_per_tenant_ip_and_globally_server_side():
    good = AsyncMock(return_value=_ai_response(json.dumps({"reply_body": "ok"})))
    with patch.object(sb.ai_provider_manager, "create_chat_completion", good):
        for _ in range(sb.sandbox_ai_budget.per_tenant):
            assert _run(sb.ask_luna("hi", "tenant-a", "1.1.1.1"))["ok"] is True
        blocked = _run(sb.ask_luna("hi", "tenant-a", "1.1.1.1"))
        assert blocked["ok"] is False and blocked["reason"] == "tenant_limit" and blocked["reply"] is None
        # a second tenant behind the same IP hits the per-IP cap next
        sb.sandbox_ai_budget = sb.SandboxAIBudget()
        for i in range(sb.sandbox_ai_budget.per_ip):
            assert _run(sb.ask_luna("hi", f"t{i}", "9.9.9.9"))["ok"] is True
        assert _run(sb.ask_luna("hi", "t-new", "9.9.9.9"))["reason"] == "ip_limit"
        # global cap
        with patch.dict(os.environ, {"SANDBOX_AI_RUNS_GLOBAL_DAY": "1"}):
            sb.sandbox_ai_budget = sb.SandboxAIBudget()
            assert _run(sb.ask_luna("hi", "g1", "2.2.2.2"))["ok"] is True
            assert _run(sb.ask_luna("hi", "g2", "3.3.3.3"))["reason"] == "global_limit"
    assert good.await_count > 0


def test_ask_luna_never_trusts_a_client_supplied_counter():
    import inspect
    assert "remaining" not in inspect.signature(sb.ask_luna).parameters


# ── 9. provider failure ──────────────────────────────────────────────────────────

def test_provider_failure_degrades_gracefully_does_not_burn_run_and_stops_retry_storms():
    failing = AsyncMock(side_effect=sb.AllProvidersFailedError([{"label": "x", "reason": "rate_limited"}]))
    with patch.object(sb.ai_provider_manager, "create_chat_completion", failing):
        for _ in range(sb.sandbox_ai_budget.max_failures):
            out = _run(sb.ask_luna("hi", "tenant-f", "4.4.4.4"))
            assert out["ok"] is False and out["reason"] == "ai_unavailable" and out["reply"]
            assert out["remaining"] == sb.sandbox_ai_budget.per_tenant  # failed run not consumed
        stopped = _run(sb.ask_luna("hi", "tenant-f", "4.4.4.4"))
    assert stopped["reason"] == "provider_unavailable"
    assert failing.await_count == sb.sandbox_ai_budget.max_failures  # no unbounded retries
    # instant demos unaffected
    assert sb.build_scenario("cancel")["pending_action"]


def test_malformed_or_empty_ai_reply_body_falls_back_never_returns_empty():
    for bad in ("not json", json.dumps({"intent": "x"}), json.dumps({"reply_body": "   "}), json.dumps(["list"])):
        with patch.object(sb.ai_provider_manager, "create_chat_completion", AsyncMock(return_value=_ai_response(bad))):
            out = _run(sb.ask_luna("hello", "tenant-m", "5.5.5.5"))
        assert out["ok"] is False and out["reply"].strip()


def test_ai_call_is_bounded_by_a_timeout():
    async def hang(**_):
        await asyncio.sleep(60)
    with patch.object(sb, "ASK_TIMEOUT_SECONDS", 0.05), patch.object(sb.ai_provider_manager, "create_chat_completion", hang):
        out = _run(sb.ask_luna("hello", "tenant-t", "6.6.6.6"))
    assert out["reason"] == "ai_unavailable"


# ── 10. real tenant path unaffected ──────────────────────────────────────────────

def test_sandbox_yields_to_production_when_shared_ai_capacity_is_low():
    calls = AsyncMock()
    with patch.object(sb, "production_headroom_ok", return_value=False), patch.object(sb.ai_provider_manager, "create_chat_completion", calls):
        out = _run(sb.ask_luna("hi", "tenant-p", "7.7.7.7"))
    assert out["reason"] == "busy" and calls.await_count == 0
    assert sb.sandbox_ai_budget.remaining("tenant-p") == sb.sandbox_ai_budget.per_tenant  # no budget burned


def test_headroom_check_reflects_shared_production_semaphore():
    from src.services.mistral_limiter import mistral_semaphore
    assert sb.production_headroom_ok() == (getattr(mistral_semaphore, "_value", 3) >= sb.PRODUCTION_HEADROOM_SLOTS)


def test_real_agent_pipeline_and_old_test_reply_endpoint_are_untouched_and_unreferenced_by_sandbox():
    src = "".join(open(p, encoding="utf-8").read() for p in SANDBOX_FILES)
    assert "process_customer_query" not in src and "generate_channel_appropriate_response" not in src
    from src.api.routes import v2_brands  # production endpoint still exists (deprecated, kept for its regression tests)
    assert hasattr(v2_brands, "test_reply")


def test_sandbox_router_exposes_expected_routes_and_ask_requires_auth():
    from src.api.routes.v2_sandbox import router
    paths = {(tuple(sorted(r.methods)), r.path) for r in router.routes}
    assert (("GET",), "/sandbox/scenarios") in paths and (("POST",), "/sandbox/ask") in paths
    ask_route = next(r for r in router.routes if r.path == "/sandbox/ask")
    assert any("get_current_tenant" in str(d.call) for d in ask_route.dependant.dependencies)
    for public in ("/sandbox/scenarios", "/sandbox/scenarios/{scenario_id}"):
        route = next(r for r in router.routes if r.path == public)
        assert not route.dependant.dependencies


# ── 11. onboarding routes to the sandbox ─────────────────────────────────────────

def test_onboarding_test_luna_step_now_opens_the_sandbox_not_the_old_endpoint():
    onboarding = open(os.path.join(BACKEND, "..", "dashboard", "src", "pages", "Onboarding.jsx"), encoding="utf-8").read()
    assert "/test-reply" not in onboarding
    assert "/sandbox" in onboarding
    app = open(os.path.join(BACKEND, "..", "dashboard", "src", "App.jsx"), encoding="utf-8").read()
    assert re.search(r'path="/sandbox/:scenarioId\?"', app)
