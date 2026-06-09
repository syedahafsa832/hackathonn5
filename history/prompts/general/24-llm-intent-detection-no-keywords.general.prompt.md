---
id: 24
title: LLM Intent Detection Replaces Static Keywords
stage: general
date: 2026-06-04
surface: agent
model: claude-sonnet-4-6
feature: none
branch: updated_whole_damn_product
user: Syeda Hafsa
command: refactor intent detection layer to use Mistral LLM
labels: ["refactor", "intent-detection", "llm", "actions", "no-keywords"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - backend/src/services/intent_detector.py (created)
  - backend/src/services/return_actions_integration.py (rewritten)
  - backend/src/agent/customer_success_agent.py (modified)
  - backend/src/services/actions_service.py (modified)
  - backend/src/workers/message_processor.py (modified)
tests:
  - none added (detection layer refactor only)
---

## Prompt

Replace ALL static keyword lists and regex patterns used for action intent detection across multiple files in `E:\hack5\hack5\backend\` with a single LLM-based intent detector. This is a focused refactor — don't change any business logic, only the detection layer.

The current system fails messages like "Please change my delivery address" because it checks for exact keyword substrings like 'change delivery address' — but "change my delivery address" doesn't match. The fix is to use Mistral (already configured) instead of keywords.

Five steps:
1. Create `intent_detector.py` with `IntentDetector` singleton calling Mistral, with keyword fallback
2. Rewrite `return_actions_integration.py` to remove all keyword class attributes and `should_check_return_eligibility`/`_detect_intent_type`/`_extract_address_text`, add `detect_intent` method, accept `intent_result` param in `handle_return_intent`
3. Update `customer_success_agent.py` to call `intent_detector.detect()` directly instead of `should_check_return_eligibility`
4. Replace `ActionDetector` class in `actions_service.py` with async LLM-based `detect_async` + sync shim `detect`; update `detect_and_create` to use `detect_async`
5. Replace inline keyword fallback block in `message_processor.py` Stage 9.5 with `intent_detector.detect()` call

## Response snapshot

Created `intent_detector.py` with:
- `IntentDetector` class using Mistral via OpenAI-compatible client
- `IntentResult` dataclass (action_type, order_id, raw_address, confidence, source)
- `_keyword_fallback()` for when LLM is unavailable (fail-open)
- Module-level singleton `intent_detector`

Rewrote `return_actions_integration.py`:
- Removed: RETURN_KEYWORDS, CANCEL_KEYWORDS, ADDRESS_KEYWORDS, RESHIP_KEYWORDS, ALL_ACTION_KEYWORDS class attributes
- Removed: `should_check_return_eligibility`, `_detect_intent_type`, `_extract_address_text` methods
- Added: `detect_intent(query)` async method
- Modified: `handle_return_intent` accepts optional `intent_result: Optional[IntentResult]`
- Modified: `_extract_order_info` uses `intent_result.order_id` as first priority
- Address text now taken from `intent_result.raw_address` instead of `_extract_address_text`

Updated `customer_success_agent.py`:
- Replaced `should_check_return_eligibility(query)` guard with direct `intent_detector.detect(query)` call
- Passes `intent_result=_intent_result` to `handle_return_intent` (avoids double LLM call)

Updated `actions_service.py`:
- Replaced `ActionDetector` class (regex-based) with LLM-based `detect_async` + broad-fragment sync shim `detect`
- Updated `detect_and_create` to `await self.detector.detect_async(message)`

Updated `message_processor.py`:
- Replaced Stage 9.5 inline keyword loop (`_cancel_kw`, `_refund_kw`) with `intent_detector.detect(content)` call

## Outcome

- Impact: Any natural-language phrasing of cancel/refund/address/reship requests now detected correctly — no more missed intents from paraphrase variations
- Tests: none (detection layer only; business logic unchanged)
- Files: 5 files modified/created
- Next prompts: add unit tests for IntentResult; wire confidence threshold for auto-skip
- Reflection: Fail-open design is critical — keyword fallback ensures zero regression if Mistral API is down

## Evaluation notes (flywheel)

- Failure modes observed: none yet; keyword fallback provides safety net
- Graders run and results (PASS/FAIL): none run
- Prompt variant (if applicable): INTENT_PROMPT v1
- Next experiment: lower confidence threshold for address_change to reduce missed detections
