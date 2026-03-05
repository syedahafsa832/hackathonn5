"""
Return Actions Integration Helper
=================================
This module shows exactly where to plug ActionsManager into your existing
customer_success_agent.py without disturbing your FAQ logic.

INTEGRATION POINTS:

1. TRIGGER DETECTION - Add keyword detection in process_customer_query (around line 85)
2. FUNCTION CALL - Add the action layer call after tool results (before response generation)
3. PROMPT UPDATE - Update _construct_v3_prompt to include return context
4. SYSTEM PROMPT - Add function definitions to enable LLM tool calling
"""
import logging
from typing import Dict, Any, Optional

from .actions_manager import actions_manager

logger = logging.getLogger(__name__)


class ReturnActionsIntegration:
    """
    Integration helper for adding return/exchange actions to the AI agent.
    Plug this into your existing customer_success_agent.py.
    """

    # Keywords that trigger return eligibility check
    RETURN_KEYWORDS = [
        'return', 'returning', 'returned',
        'refund', 'refunding', 'refunded',
        'exchange', 'exchanging', 'exchanged',
        'wrong size', 'doesn\'t fit', 'doesnt fit',
        'too small', 'too big', 'too large',
        'didn\'t like', 'didnt like',
        'wrong item', 'different size'
    ]

    def __init__(self):
        self.actions = actions_manager

    def should_check_return_eligibility(self, query: str) -> bool:
        """Detect if the query mentions return/refund/exchange intent."""
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in self.RETURN_KEYWORDS)

    async def handle_return_intent(
        self,
        query: str,
        customer_info: Dict[str, Any],
        existing_tool_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Main integration point. Call this in your process_customer_query
        after getting tool_results but before response generation.

        Args:
            query: The customer's message
            customer_info: Customer data from your system
            existing_tool_results: Results from your existing V3 tools

        Returns:
            Dictionary with:
            - return_checked: bool - whether we performed a return check
            - eligibility: dict - result from check_return_eligibility
            - exchange: dict - result from suggest_exchange (if applicable)
            - action_context: str - natural language context for the LLM
        """
        result = {
            "return_checked": False,
            "eligibility": None,
            "exchange": None,
            "action_context": ""
        }

        # Extract order info from query or existing tools
        order_id, email = self._extract_order_info(query, customer_info, existing_tool_results)

        if not order_id or not email:
            # Can't check without order and email
            result["action_context"] = (
                "Customer mentioned return/exchange but we need their order number and email "
                "to verify eligibility. Ask for these details."
            )
            return result

        # Step 1: Check return eligibility
        eligibility = await self.actions.check_return_eligibility(order_id, email)
        result["eligibility"] = eligibility
        result["return_checked"] = True

        # Step 2: If eligible, check for exchange options
        if eligibility.get("eligible"):
            # Check if they mentioned size issues
            size_issue = self._is_size_issue(query)

            if size_issue:
                # Extract preferred size from query
                preferred_size = self._extract_size_preference(query)

                exchange = await self.actions.suggest_exchange(eligibility, preferred_size)
                result["exchange"] = exchange

                # Build action context for the LLM
                result["action_context"] = self._build_action_context(eligibility, exchange)
            else:
                # Eligible but not size-related - just confirm eligibility
                result["action_context"] = (
                    f"Return eligibility confirmed: {eligibility.get('reason')}"
                )
        else:
            # Not eligible - explain why and DON'T offer exchange
            result["action_context"] = (
                f"Return NOT eligible: {eligibility.get('reason')}. "
                "Do NOT offer exchange. Acknowledge and offer to escalate to human support if frustrated."
            )

        return result

    def _extract_order_info(
        self,
        query: str,
        customer_info: Dict[str, Any],
        existing_tool_results: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract order ID and email from various sources."""
        import re

        email = customer_info.get("email")

        # Try to extract order number from query
        order_id = None
        order_patterns = [
            r'order\s*#?(\d+)',
            r'#(\d{4,})',
            r'(\d{4,})'
        ]

        for pattern in order_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                order_id = match.group(1)
                break

        # If we have existing order data from earlier in the conversation
        if not order_id and existing_tool_results.get("orders_by_email"):
            orders = existing_tool_results["orders_by_email"].get("orders", [])
            if orders:
                # Use most recent order
                order_id = orders[0].get("order_number")

        # If we found order but no email, try from existing orders
        if order_id and not email:
            if existing_tool_results.get("orders_by_email"):
                email = existing_tool_results["orders_by_email"].get("email")

        return order_id, email

    def _is_size_issue(self, query: str) -> bool:
        """Check if the return is due to size issues."""
        size_keywords = [
            'wrong size', 'doesn\'t fit', 'doesnt fit',
            'too small', 'too big', 'too large',
            'different size', 'other size',
            'size', 'small', 'medium', 'large', 'xl'
        ]
        query_lower = query.lower()
        return any(kw in query_lower for kw in size_keywords)

    def _extract_size_preference(self, query: str) -> Optional[str]:
        """Extract the size the customer wants from the query."""
        import re

        # Look for size indicators
        size_patterns = [
            r'(extra\s*small|xs)',
            r'(small|s(?!mall))',
            r'(medium|m(?!edium))',
            r'(large|l(?!arge))',
            r'(extra\s*large|xl)',
            r'(xxl|double\s*xl)',
            r'(size\s*(small|medium|large|xs|xl))'
        ]

        for pattern in size_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                size_text = match.group(1).lower()
                # Normalize
                if 'xs' in size_text or 'extra small' in size_text:
                    return 'XS'
                elif 'small' in size_text and 'extra' not in size_text:
                    return 'S'
                elif 'medium' in size_text or size_text == 'm':
                    return 'M'
                elif 'large' in size_text and 'extra' not in size_text:
                    return 'L'
                elif 'xl' in size_text or 'extra large' in size_text:
                    return 'XL'
                elif 'xxl' in size_text:
                    return 'XXL'

        return None

    def _build_action_context(
        self,
        eligibility: Dict[str, Any],
        exchange: Dict[str, Any]
    ) -> str:
        """Build natural language context for the LLM about the return/exchange."""
        if not exchange or not exchange.get("has_exchange"):
            return f"Return eligible. {eligibility.get('reason')}"

        suggestions = exchange.get("suggestions", [])
        if not suggestions:
            return f"Return eligible but no exchanges available. {eligibility.get('reason')}"

        # Build suggestion text
        parts = ["Return eligible! "]

        for s in suggestions:
            direction = "larger" if s["direction"] > 0 else "smaller"
            parts.append(
                f"We have {s['original_item']} in {s['suggested_size']} "
                f"(one size {direction}) available."
            )

        parts.append(f"\n{exchange.get('pitch', '')}")

        return " ".join(parts)

    def get_updated_system_prompt_addition(self) -> str:
        """
        Returns additional system prompt instructions to add to your
        _construct_v3_prompt method.
        """
        return """
        RETURN/EXCHANGE HANDLING:
        - When customer mentions return, refund, exchange, or size issues, use the check_return_eligibility function first
        - If eligible AND they want a different size, use suggest_exchange to find alternatives
        - If NOT eligible, do NOT offer exchange—acknowledge the policy and offer to escalate
        - Never make up return eligibility—always verify with the function
        - Use the action_context provided to guide your response naturally
        - For exchanges, present it as "I'd love to help you find the perfect fit" rather than processing a return
        """

    def get_function_calling_instructions(self) -> str:
        """
        Returns instructions for enabling function calling in your LLM.
        Add these to where you configure your LLM tools.
        """
        return """
        TOOL USE RULES:
        1. ALWAYS call check_return_eligibility when customer mentions return/refund/exchange
        2. If eligibility.eligible is true AND customer wants different size, call suggest_exchange
        3. If eligibility.eligible is false, do NOT call suggest_exchange
        4. Use the function responses naturally in your response—don't just list the JSON
        """


# Singleton instance
return_actions = ReturnActionsIntegration()
