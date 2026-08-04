"""
Reply Style presets — cold-start solution for merchants with no support
history yet. Hand-authored, capped at five, no LLM call required to use one.

Each preset's `as_style_dict()` uses the same key shape as a learned Reply
Style profile (see reply_style_service.generate_learned_profile), so
build_style_prompt_block() in reply_style_service.py can render either one
identically. Presets never change business logic — only wording, tone, and
formatting.
"""
from dataclasses import dataclass
from typing import List, Dict


@dataclass(frozen=True)
class ReplyStylePreset:
    id: str
    label: str
    description: str
    example_replies: List[str]
    tone: str
    greeting_style: str
    closing_style: str
    emoji_usage: str
    sentence_length: str
    paragraph_style: str
    use_bullets: bool
    use_customer_name: str

    def as_style_dict(self) -> Dict:
        return {
            "tone": self.tone,
            "greeting_style": self.greeting_style,
            "closing_style": self.closing_style,
            "emoji_usage": self.emoji_usage,
            "sentence_length": self.sentence_length,
            "paragraph_style": self.paragraph_style,
            "use_bullets": self.use_bullets,
            "use_customer_name": self.use_customer_name,
        }


WARM_FRIENDLY = ReplyStylePreset(
    id="warm_friendly",
    label="Warm & Friendly",
    description="Casual and personable, like texting a helpful friend.",
    example_replies=[
        "Hey Sam! Totally get the frustration, let's sort this out. Your order shipped yesterday and should land by Friday.",
        "Thanks so much for reaching out! I've got you, this one's an easy fix. Sending a fresh replacement your way today.",
    ],
    tone="casual, warm, empathetic",
    greeting_style="first name, casual (Hey {name}!)",
    closing_style="Thanks!",
    emoji_usage="occasional, light (max one per reply)",
    sentence_length="short",
    paragraph_style="single short paragraph",
    use_bullets=False,
    use_customer_name="always, by first name",
)

PROFESSIONAL = ReplyStylePreset(
    id="professional",
    label="Professional",
    description="Polished and courteous, standard business support tone.",
    example_replies=[
        "Hello Sam, thank you for contacting us. Your order has shipped and is expected to arrive within 2-3 business days.",
        "Hello, I understand your concern and I'm happy to help. A replacement has been arranged and will ship today.",
    ],
    tone="polished, courteous, measured",
    greeting_style="first name, formal (Hello {name},)",
    closing_style="Best regards,",
    emoji_usage="none",
    sentence_length="medium",
    paragraph_style="one to two short paragraphs",
    use_bullets=True,
    use_customer_name="yes, by first name",
)

SHORT_DIRECT = ReplyStylePreset(
    id="short_direct",
    label="Short & Direct",
    description="No fluff. Answer first, minimal words.",
    example_replies=[
        "Shipped yesterday, arrives Friday. Tracking below.",
        "Replacement is on its way, arrives in 3-5 days. Let me know if anything else comes up.",
    ],
    tone="direct, efficient, plain",
    greeting_style="minimal or none",
    closing_style="none or a single line",
    emoji_usage="none",
    sentence_length="very short",
    paragraph_style="single line or two",
    use_bullets=True,
    use_customer_name="rarely",
)

PREMIUM_LUXURY = ReplyStylePreset(
    id="premium_luxury",
    label="Premium / Luxury",
    description="Refined and attentive, white-glove service tone.",
    example_replies=[
        "Dear Sam, thank you for your patience. Your order is on its way and should arrive by Friday, we'll be monitoring it closely on your behalf.",
        "It would be our pleasure to arrange a replacement for you right away. You'll receive tracking details shortly.",
    ],
    tone="refined, attentive, gracious",
    greeting_style="first name, gracious (Dear {name},)",
    closing_style="Warm regards,",
    emoji_usage="none",
    sentence_length="medium to long",
    paragraph_style="flowing, one to two paragraphs",
    use_bullets=False,
    use_customer_name="always, by first name",
)

PLAYFUL = ReplyStylePreset(
    id="playful",
    label="Playful",
    description="Upbeat and fun, light personality in every reply.",
    example_replies=[
        "Heyyy Sam! Good news, your order's already on its way and should be at your door by Friday!",
        "Oh no, let's fix that right up! Sending a shiny new replacement your way today.",
    ],
    tone="upbeat, energetic, fun",
    greeting_style="first name, enthusiastic (Heyyy {name}!)",
    closing_style="Talk soon!",
    emoji_usage="frequent, expressive",
    sentence_length="short",
    paragraph_style="single short paragraph",
    use_bullets=False,
    use_customer_name="always, by first name",
)

PRESETS: Dict[str, ReplyStylePreset] = {
    p.id: p
    for p in [WARM_FRIENDLY, PROFESSIONAL, SHORT_DIRECT, PREMIUM_LUXURY, PLAYFUL]
}

DEFAULT_PRESET_ID = "warm_friendly"


def get_preset(preset_id: str) -> ReplyStylePreset:
    return PRESETS.get(preset_id, PRESETS[DEFAULT_PRESET_ID])


def list_presets() -> List[Dict]:
    """Catalog shape for the Reply Style settings UI."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "example_replies": p.example_replies,
        }
        for p in PRESETS.values()
    ]
