"""
Tenant-isolation check on V3Tools.get_product_details / check_inventory
(src/services/tools.py), flagged for review.

Finding: both methods look up `variants`/`products`/`inventory` by SKU or
variant_id/location_name alone, with no tenant_id/brand_id filter anywhere
in the query - if two different merchants' stores ever used the same SKU,
either method would happily return the wrong tenant's product/inventory
data to whoever asked. In isolation, that's a real bug pattern.

But neither method is reachable from the live agent flow: the only
Shopify-agent entry point, customer_success_agent.py, never calls
v3_tools.get_product_details or v3_tools.check_inventory - every product/
inventory tool call it actually makes (get_inventory_status,
get_product_recommendations, discover_products_by_category) goes through
methods that take shop_domain/access_token per-call (resolved from the
specific brand row for that conversation - see
_brand_shopify_domain/_brand_shopify_token below) and hit the live Shopify
Admin API directly, never the local products/variants/inventory tables.
Those tables also aren't defined in any tracked migration - this looks
like dead code from a pre-multi-tenant single-shop demo, not part of the
current architecture.

These tests pin that: (1) the two unscoped methods stay out of the live
dispatch path, and (2) every product/inventory method the agent DOES call
is called with brand-scoped credentials, not a bare SKU/location lookup
against the shared local tables. If either regresses, this is the exact
cross-tenant leak the flagged issue described made live.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_FILE = os.path.join(_REPO_ROOT, "src", "agent", "customer_success_agent.py")
_TOOLS_FILE = os.path.join(_REPO_ROOT, "src", "services", "tools.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_get_product_details_and_check_inventory_are_not_called_by_the_live_agent():
    """The two methods with no tenant_id/brand_id scoping must never be
    wired into the agent's actual tool dispatch. If this test starts
    failing, whoever added the call site MUST add brand-scoped filtering
    (or route through the live Shopify API like every other product tool
    already does) before this can be considered safe again."""
    agent_src = _read(_AGENT_FILE)
    assert "v3_tools.get_product_details(" not in agent_src
    assert "v3_tools.check_inventory(" not in agent_src


def test_get_product_details_and_check_inventory_have_no_other_callers_in_the_app():
    """Confirms these methods are unreferenced anywhere outside their own
    definition - i.e. genuinely unreachable dead code, not just unused by
    this one agent file."""
    for root, _dirs, files in os.walk(os.path.join(_REPO_ROOT, "src")):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            if path == _TOOLS_FILE:
                continue
            src = _read(path)
            assert "get_product_details(" not in src, f"unexpected caller in {path}"
            assert ".check_inventory(" not in src, f"unexpected caller in {path}"


def test_live_product_and_inventory_tool_methods_require_per_brand_shopify_credentials():
    """Every product/inventory method the agent actually calls
    (get_inventory_status, get_product_recommendations,
    discover_products_by_category) must take shop_domain/access_token as
    parameters - the pattern that keeps them scoped to whichever brand's
    conversation is calling them, instead of a shared, unscoped local
    table lookup."""
    tools_src = _read(_TOOLS_FILE)
    for method in ("get_inventory_status", "get_product_recommendations", "discover_products_by_category"):
        match = re.search(rf"async def {method}\(([^)]*)\)", tools_src, re.DOTALL)
        assert match, f"{method} not found in tools.py"
        signature = match.group(1)
        assert "shop_domain" in signature, f"{method} lost its shop_domain scoping"
        assert "access_token" in signature, f"{method} lost its access_token scoping"


def test_agent_resolves_shopify_credentials_from_the_conversations_own_brand():
    """_brand_shopify_domain/_brand_shopify_token (passed into every live
    product/inventory tool call) must come from a brand row looked up for
    this conversation - never a bare global/env-var shop, which would tie
    every tenant's product lookups to one hardcoded store."""
    agent_src = _read(_AGENT_FILE)
    assert "_brand_shopify_domain = None" in agent_src
    assert "_brand_shopify_domain = _b[0].get(\"shopify_domain\")" in agent_src
    # Every live call site passes the resolved, brand-specific values through -
    # never SHOPIFY_SHOP_NAME/SHOPIFY_ACCESS_TOKEN env vars.
    assert "shop_domain=_brand_shopify_domain" in agent_src
    assert "access_token=_brand_shopify_token" in agent_src


def test_no_migration_defines_the_legacy_products_variants_inventory_tables():
    """products/variants/inventory (what the two unscoped methods query)
    aren't part of this repo's tracked schema at all - further evidence
    this is leftover single-tenant demo code, not the live multi-tenant
    data model (which is Shopify-API-first, not a local product mirror)."""
    migrations_dir = os.path.join(_REPO_ROOT, "migrations")
    for fname in os.listdir(migrations_dir):
        if not fname.endswith(".sql"):
            continue
        sql = _read(os.path.join(migrations_dir, fname)).lower()
        assert "create table variants" not in sql, fname
        assert "create table inventory " not in sql and not sql.strip().startswith("create table inventory("), fname
