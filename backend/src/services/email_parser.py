"""
Email Parser Service
Parses incoming emails and extracts relevant information for ticket creation.
"""

import re
from typing import Optional
from pydantic import BaseModel


class ParsedEmail(BaseModel):
    """Parsed email data for ticket creation"""
    customer_email: str
    subject: str
    body: str
    order_number: Optional[str] = None
    customer_name: Optional[str] = None


class EmailParser:
    """Service to parse incoming emails and extract ticket data"""

    # Regex patterns for order number extraction
    ORDER_PATTERNS = [
        r'(?:order|order\s*#|order\s*id|#)\s*[:\-]?\s*([A-Z0-9]{4,20})',
        r'(?:order|order\s*#)\s*([0-9]{6,12})',
        r'#(\d{6,12})',
        r'(\d{8,15})',  # Common Shopify order numbers
    ]

    def __init__(self):
        self.compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.ORDER_PATTERNS]

    def parse(self, raw_email: dict) -> ParsedEmail:
        """
        Parse raw email data into structured format.

        Args:
            raw_email: Dictionary with 'from', 'subject', 'body', 'text' keys

        Returns:
            ParsedEmail with extracted data
        """
        # Extract customer email from 'from' field
        customer_email = self._extract_email(raw_email.get('from', ''))

        # Extract customer name if available
        customer_name = self._extract_name(raw_email.get('from', ''))

        # Get subject and body
        subject = raw_email.get('subject', '')
        body = raw_email.get('body', '') or raw_email.get('text', '')

        # Extract order number from subject or body
        order_number = self._extract_order_number(f"{subject}\n{body}")

        return ParsedEmail(
            customer_email=customer_email,
            subject=subject,
            body=body,
            order_number=order_number,
            customer_name=customer_name
        )

    def _extract_email(self, from_field: str) -> str:
        """Extract email address from 'From' header"""
        # Match email pattern in angle brackets or plain
        match = re.search(r'<([^>]+)>|([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', from_field)
        if match:
            return match.group(1) or match.group(2)
        return from_field.strip()

    def _extract_name(self, from_field: str) -> Optional[str]:
        """Extract display name from 'From' header"""
        # Pattern: "Name" <email@domain.com> or Name <email@domain.com>
        match = re.search(r'^"?([^"<]+)"?\s*<', from_field)
        if match:
            return match.group(1).strip().strip('"')
        return None

    def _extract_order_number(self, text: str) -> Optional[str]:
        """Extract order number from email text using patterns"""
        for pattern in self.compiled_patterns:
            match = pattern.search(text)
            if match:
                return match.group(1).upper()
        return None

    def parse_for_tenant(self, raw_email: dict, tenant_domain: str) -> ParsedEmail:
        """
        Parse email and determine tenant from recipient address.

        Args:
            raw_email: Raw email data
            tenant_domain: Expected tenant domain (e.g., 'brand.com')

        Returns:
            ParsedEmail with extracted data
        """
        parsed = self.parse(raw_email)

        # Additional tenant-specific processing can be added here
        # For example, verifying the email was sent to the correct tenant address

        return parsed


# Singleton instance
email_parser = EmailParser()