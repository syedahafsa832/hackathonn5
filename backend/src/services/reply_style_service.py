"""
Reply Style — per-brand wording/tone personalization.

Explicitly out of scope (per spec): this never touches business logic,
refund eligibility, escalation decisions, or safety rules. It only decides
how a reply is *worded*. Facts always come from elsewhere in the prompt and
always win.

Three modes, never blended:
- preset:   one of the five hand-authored constants in reply_style_presets.py
- learned:  a profile extracted from the brand's own approved replies
- disabled: no style block injected, agent falls back to a neutral default

Switching preset -> learned is a full replacement, never a merge (per spec).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.lib.supabase_client import supabase_select, supabase_update
from src.agent.reply_style_presets import get_preset, DEFAULT_PRESET_ID

logger = logging.getLogger(__name__)

MIN_APPROVED_REPLIES_TO_LEARN = 20
REGENERATE_AFTER_NEW_REPLIES = 15
REGENERATE_AFTER_DAYS = 7
MAX_REPLIES_FOR_PROFILE_GENERATION = 50

STYLE_PROFILE_KEYS = [
    "tone", "greeting_style", "closing_style", "emoji_usage",
    "sentence_length", "paragraph_style", "use_bullets", "use_customer_name",
]


def _approved_reply_texts(brand_id: str, limit: int = MAX_REPLIES_FOR_PROFILE_GENERATION) -> List[str]:
    """Human-approved/sent reply bodies, newest first. A reply counts if a
    human either approved it as-is (human_approved) or edited it before
    sending (human_response) — both mean a person stood behind the wording."""
    tickets = supabase_select("tickets", {
        "brand_id": f"eq.{brand_id}",
        "or": "(human_approved.is.true,human_response.not.is.null)",
        "order": "updated_at.desc",
        "limit": str(limit),
    })
    texts = []
    for t in tickets or []:
        text = (t.get("human_response") or t.get("ai_reply") or "").strip()
        if text:
            texts.append(text)
    return texts


def _uploaded_example_texts(brand_id: str, limit: int = MAX_REPLIES_FOR_PROFILE_GENERATION) -> List[str]:
    rows = supabase_select("reply_style_examples", {
        "brand_id": f"eq.{brand_id}",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    return [r["content"].strip() for r in (rows or []) if r.get("content", "").strip()]


def count_eligible_approved_replies(brand_id: str) -> int:
    return len(_approved_reply_texts(brand_id, limit=1000))


def count_approved_replies_since(brand_id: str, since_iso: Optional[str]) -> int:
    """New approved replies since a timestamp (updated_at is set on both the
    approve-ai and manual send-reply paths, so it's a reliable 'became
    approved' marker regardless of which path was used)."""
    params = {
        "brand_id": f"eq.{brand_id}",
        "or": "(human_approved.is.true,human_response.not.is.null)",
        "select": "id",
    }
    if since_iso:
        params["updated_at"] = f"gt.{since_iso}"
    rows = supabase_select("tickets", params)
    return len(rows or [])


def get_brand_reply_style(brand_id: str) -> Optional[Dict]:
    rows = supabase_select("brands", {"id": f"eq.{brand_id}"})
    return rows[0] if rows else None


def get_active_style(brand: Dict) -> Optional[Dict]:
    """Returns the style dict currently in effect for this brand, or None if
    Reply Style is disabled (agent should fall back to a neutral baseline)."""
    mode = brand.get("reply_style_mode") or "preset"
    if mode == "disabled":
        return None
    if mode == "learned" and brand.get("reply_style_profile"):
        return brand["reply_style_profile"]
    preset = get_preset(brand.get("reply_style_preset") or DEFAULT_PRESET_ID)
    return preset.as_style_dict()


def build_style_prompt_block(style: Optional[Dict]) -> str:
    """Renders a style dict into the prompt fragment injected by
    customer_success_agent.py. Wording/formatting only — never touches
    facts, actions, or safety rules, which live in separate fixed sections
    of the prompt."""
    if not style:
        return (
            "TONE: friendly and helpful, no strong stylistic preference set.\n"
            "Keep messages short (3-4 sentences), avoid corporate jargon."
        )

    bullets_line = (
        "You may use short bullet points when listing multiple items."
        if style.get("use_bullets")
        else "Never use bullet points or numbered lists — write in plain sentences."
    )

    return (
        f"TONE: {style.get('tone', 'warm and helpful')}.\n"
        f"GREETING: {style.get('greeting_style', 'first name')}.\n"
        f"CLOSING/SIGN-OFF: {style.get('closing_style', 'a brief friendly close')}.\n"
        f"EMOJI USE: {style.get('emoji_usage', 'sparingly, if at all')}.\n"
        f"SENTENCE LENGTH: {style.get('sentence_length', 'short')}.\n"
        f"PARAGRAPH STRUCTURE: {style.get('paragraph_style', 'a short paragraph or two')}.\n"
        f"{bullets_line}\n"
        f"USE OF CUSTOMER'S NAME: {style.get('use_customer_name', 'when natural')}."
    )


def switch_to_learned(brand_id: str) -> Dict:
    """Full replacement, never a merge with the preset — per spec."""
    brand = get_brand_reply_style(brand_id)
    if not brand or not brand.get("reply_style_profile"):
        return {"success": False, "error": "No learned profile available yet."}
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "reply_style_mode": "learned",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True}


def _build_extraction_prompt(reply_texts: List[str]) -> List[Dict]:
    numbered = "\n\n".join(f"[{i+1}] {t}" for i, t in enumerate(reply_texts))
    system = (
        "You analyze a support team's past customer replies and extract their "
        "natural writing style. You are NOT judging correctness or policy — only "
        "wording, tone, and formatting habits. Respond with strict JSON only, no "
        "markdown fences, matching this exact shape:\n"
        '{"tone": string, "greeting_style": string, "closing_style": string, '
        '"emoji_usage": string, "sentence_length": string, "paragraph_style": string, '
        '"use_bullets": boolean, "use_customer_name": string, '
        '"reasoning": ["short bullet describing one detected habit", "..."]}\n'
        "Keep each style field to a short phrase (under 10 words). reasoning should "
        "be 3-6 short human-readable bullets a merchant could read to understand "
        "what was detected, e.g. \"Usually greets customers by first name\"."
    )
    user = f"Past approved customer support replies:\n\n{numbered}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_learned_profile(brand_id: str, force: bool = False) -> Dict:
    """Extracts a structured Reply Style profile from the brand's approved
    replies (plus uploaded examples, if configured to use them). One LLM
    call, no business-logic content — style attributes only. The reasoning
    text is stored for merchant/debugging visibility and is never injected
    into the live agent prompt."""
    brand = get_brand_reply_style(brand_id)
    if not brand:
        return {"success": False, "error": "Brand not found"}

    texts = []
    if not brand.get("reply_style_use_uploaded_only"):
        texts = _approved_reply_texts(brand_id)
    texts = texts + _uploaded_example_texts(brand_id)

    if not force and len(texts) < MIN_APPROVED_REPLIES_TO_LEARN:
        return {
            "success": False,
            "error": f"Need at least {MIN_APPROVED_REPLIES_TO_LEARN} approved replies "
                     f"(have {len(texts)}).",
        }
    if not texts:
        return {"success": False, "error": "No approved replies or examples to learn from."}

    from src.services.ai_provider_manager import ai_provider_manager, AllProvidersFailedError

    if not ai_provider_manager.has_providers:
        return {"success": False, "error": "No AI provider configured."}

    messages = _build_extraction_prompt(texts[:MAX_REPLIES_FOR_PROFILE_GENERATION])
    try:
        response, _label, _model = await ai_provider_manager.create_chat_completion(
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except AllProvidersFailedError as e:
        logger.error(f"[ReplyStyle] Profile generation failed, all providers down: {e}")
        return {"success": False, "error": "All connected AI models are at capacity right now — try again in a few minutes."}

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"[ReplyStyle] Could not parse profile JSON for brand {brand_id}: {e}")
        return {"success": False, "error": "Could not parse generated style profile."}

    profile = {k: parsed.get(k) for k in STYLE_PROFILE_KEYS}
    reasoning_lines = parsed.get("reasoning") or []
    reasoning_text = "\n".join(f"• {line}" for line in reasoning_lines) if reasoning_lines else ""

    now_iso = datetime.now(timezone.utc).isoformat()
    supabase_update("brands", {"id": f"eq.{brand_id}"}, {
        "reply_style_profile": profile,
        "reply_style_reasoning": reasoning_text,
        "reply_style_last_generated_at": now_iso,
        "updated_at": now_iso,
    })
    return {"success": True, "profile": profile, "reasoning": reasoning_text}


async def regenerate_if_due(brand_id: str) -> None:
    """Checked opportunistically (e.g. when the Settings page loads) rather
    than on a scheduler — no cron infra exists in this codebase. Silent
    no-op if not due or if learning is off; never raises, since this is a
    best-effort background refresh, not a user-facing action."""
    try:
        brand = get_brand_reply_style(brand_id)
        if not brand or not brand.get("reply_style_learn_automatically", True):
            return
        if brand.get("reply_style_mode") == "disabled":
            return

        last_generated = brand.get("reply_style_last_generated_at")
        due_by_time = False
        if last_generated:
            last_dt = datetime.fromisoformat(last_generated.replace("Z", "+00:00"))
            due_by_time = datetime.now(timezone.utc) - last_dt > timedelta(days=REGENERATE_AFTER_DAYS)
        else:
            due_by_time = True

        has_profile = bool(brand.get("reply_style_profile"))
        if has_profile:
            new_since_last = count_approved_replies_since(brand_id, last_generated)
            due_by_volume = new_since_last >= REGENERATE_AFTER_NEW_REPLIES
        else:
            due_by_volume = count_eligible_approved_replies(brand_id) >= MIN_APPROVED_REPLIES_TO_LEARN

        if due_by_volume or (has_profile and due_by_time):
            await generate_learned_profile(brand_id)
    except Exception as e:
        logger.warning(f"[ReplyStyle] regenerate_if_due failed for brand {brand_id}: {e}")
