#!/usr/bin/env python3
"""
Customer Success AI Agent Prototype

Simple Python script that demonstrates core functionality:
- Basic customer interaction loop
- Channel metadata handling
- Simple keyword-based knowledge search
- Sentiment analysis (basic - detect negative words)
- Escalation decision logic
- Channel-aware response formatting
"""

import argparse
import json
import re
from typing import Dict, List, Tuple
import os

class KnowledgeBase:
    """Simple in-memory knowledge base for prototype"""

    def __init__(self):
        self.articles = [
            {
                "id": 1,
                "title": "Password Reset",
                "content": "To reset your password, go to the login page and click 'Forgot Password'. Enter your email address and follow the instructions sent to your inbox.",
                "category": "account",
                "tags": ["password", "login", "reset", "account"]
            },
            {
                "id": 2,
                "title": "API Authentication",
                "content": "To authenticate with our API, you need an API key. Find your API key in the dashboard under Settings > API Keys. Include it in the Authorization header as 'Bearer YOUR_API_KEY'.",
                "category": "technical",
                "tags": ["api", "authentication", "key", "developer"]
            },
            {
                "id": 3,
                "title": "Subscription Management",
                "content": "Manage your subscription in the dashboard under Billing > Subscription. You can upgrade, downgrade, or cancel your plan at any time. Changes take effect at the next billing cycle.",
                "category": "billing",
                "tags": ["subscription", "billing", "upgrade", "cancel"]
            },
            {
                "id": 4,
                "title": "Integration Setup",
                "content": "To set up integrations, visit the Integrations page in your dashboard. Select the service you want to connect and follow the OAuth flow. For custom integrations, refer to our API documentation.",
                "category": "technical",
                "tags": ["integration", "api", "oauth", "connect"]
            },
            {
                "id": 5,
                "title": "User Management",
                "content": "Add team members in Settings > Team. Invite users by email and assign roles (Admin, Member, Viewer). Admins can manage all settings while Members can only access their assigned projects.",
                "category": "account",
                "tags": ["team", "users", "roles", "permissions"]
            }
        ]

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """Simple keyword-based search"""
        query_lower = query.lower()
        results = []

        for article in self.articles:
            # Score based on keyword matches
            score = 0
            content_lower = article['content'].lower()

            # Count matches in title and content
            score += query_lower.count(article['title'].lower()) * 5
            score += content_lower.count(query_lower) * 3

            # Count matches in tags
            for tag in article['tags']:
                if tag in query_lower:
                    score += 2

            if score > 0:
                results.append({
                    **article,
                    'relevance_score': score
                })

        # Sort by relevance and return top_k
        results.sort(key=lambda x: x['relevance_score'], reverse=True)
        return results[:top_k]


class SentimentAnalyzer:
    """Basic sentiment analyzer for prototype"""

    def __init__(self):
        self.positive_words = [
            'good', 'great', 'excellent', 'awesome', 'fantastic', 'love',
            'perfect', 'amazing', 'wonderful', 'brilliant', 'happy',
            'satisfied', 'pleased', 'thank you', 'thanks', 'nice'
        ]

        self.negative_words = [
            'bad', 'terrible', 'awful', 'horrible', 'hate', 'stupid',
            'useless', 'broken', 'error', 'problem', 'issue', 'bug',
            'disappointed', 'frustrated', 'angry', 'annoyed', 'sucks',
            'worst', 'horrific', 'ridiculous', 'pathetic', 'garbage'
        ]

    def analyze_sentiment(self, text: str) -> float:
        """Return sentiment score between -1 (very negative) and 1 (very positive)"""
        text_lower = text.lower()

        pos_count = sum(1 for word in self.positive_words if word in text_lower)
        neg_count = sum(1 for word in self.negative_words if word in text_lower)

        # Normalize to -1 to 1 range
        total_words = len(text_lower.split())
        if total_words == 0:
            return 0.0

        sentiment_score = (pos_count - neg_count) / max(total_words / 10, 1)

        # Clamp between -1 and 1
        return max(-1.0, min(1.0, sentiment_score))


class EscalationDetector:
    """Detect when to escalate to human agent"""

    def __init__(self):
        self.pricing_keywords = [
            'price', 'cost', 'pricing', 'charge', 'pay', 'payment', 'money',
            'billing', 'invoice', 'subscription cost', 'enterprise price', 'quote'
        ]

        self.legal_keywords = [
            'legal', 'lawyer', 'sue', 'lawsuit', 'contract', 'agreement',
            'terms', 'conditions', 'compliance', 'regulation', 'court'
        ]

        self.refund_keywords = [
            'refund', 'return', 'money back', 'cancel charge', 'reversal',
            'dispute', 'chargeback', 'credit', 'compensation'
        ]

        self.escalation_keywords = [
            'manager', 'supervisor', 'ceo', 'executive', 'director',
            'escalate', 'complaint', 'issue', 'problem', 'urgent'
        ]

        self.profanity_list = [
            'fuck', 'shit', 'damn', 'bitch', 'asshole', 'bastard', 'cunt',
            'dick', 'piss', 'damn', 'bloody', 'bollocks', 'arsehole'
        ]

    def should_escalate(self, text: str) -> Tuple[bool, str]:
        """Check if message should be escalated and return reason"""
        text_lower = text.lower()

        # Check pricing keywords
        for keyword in self.pricing_keywords:
            if keyword in text_lower:
                return True, f"Pricing inquiry: {keyword}"

        # Check legal keywords
        for keyword in self.legal_keywords:
            if keyword in text_lower:
                return True, f"Legal matter: {keyword}"

        # Check refund keywords
        for keyword in self.refund_keywords:
            if keyword in text_lower:
                return True, f"Refund request: {keyword}"

        # Check profanity
        for profanity in self.profanity_list:
            if profanity in text_lower:
                return True, f"Profanity detected: {profanity}"

        # Check escalation keywords
        for keyword in self.escalation_keywords:
            if keyword in text_lower:
                return True, f"Escalation requested: {keyword}"

        return False, ""


class CustomerSuccessPrototype:
    """Main prototype class"""

    def __init__(self):
        self.kb = KnowledgeBase()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.escalation_detector = EscalationDetector()
        self.conversation_history = []

    def format_response_for_channel(self, response: str, channel: str) -> str:
        """Format response based on channel requirements"""
        if channel == 'email':
            # Formal email response
            formatted = f"Dear Valued Customer,\n\n{response}\n\nBest regards,\nCustomer Success AI Agent\nAutomated Response System"

            # Truncate if too long (500 words max)
            words = formatted.split()
            if len(words) > 500:
                formatted = ' '.join(words[:500]) + "... [Message truncated per policy]"

        elif channel == 'whatsapp':
            # Concise WhatsApp response (300 chars max)
            formatted = response[:300] if len(response) > 300 else response

        elif channel == 'web_form':
            # Semi-formal web response
            formatted = response
        else:
            formatted = response

        return formatted

    def process_customer_query(self, query: str, channel: str = 'web_form') -> Dict:
        """Process customer query and return response"""
        result = {
            'query': query,
            'channel': channel,
            'sentiment_score': 0,
            'escalated': False,
            'escalation_reason': '',
            'response': '',
            'knowledge_base_results': []
        }

        # Analyze sentiment
        sentiment_score = self.sentiment_analyzer.analyze_sentiment(query)
        result['sentiment_score'] = sentiment_score

        # Check for escalation
        should_escalate, escalation_reason = self.escalation_detector.should_escalate(query)
        if should_escalate:
            result['escalated'] = True
            result['escalation_reason'] = escalation_reason
            result['response'] = "I understand this is an important matter. Let me connect you with a human agent who can assist you further."
            return result

        # Search knowledge base
        kb_results = self.kb.search(query, top_k=3)
        result['knowledge_base_results'] = kb_results

        # Generate response based on knowledge base results
        if kb_results:
            # Use the highest scoring result
            best_match = kb_results[0]
            response = f"I found this information that might help:\n\n{best_match['title']}\n{best_match['content']}"

            if len(kb_results) > 1:
                response += f"\n\nI also found these related articles: {[r['title'] for r in kb_results[1:]]}"
        else:
            response = "I couldn't find specific information about your query. Let me connect you with a human agent who can provide personalized assistance."
            # Still escalate if no knowledge base match and sentiment is not positive
            if sentiment_score < 0.2:
                result['escalated'] = True
                result['escalation_reason'] = "No KB match and negative sentiment"

        result['response'] = self.format_response_for_channel(response, channel)
        return result


def main():
    parser = argparse.ArgumentParser(description='Customer Success AI Agent Prototype')
    parser.add_argument('--test', action='store_true', help='Run test mode')
    parser.add_argument('--interactive', '-i', action='store_true', help='Run in interactive mode')

    args = parser.parse_args()

    agent = CustomerSuccessPrototype()

    if args.test:
        print("Running prototype tests...\n")

        # Test cases
        test_cases = [
            {
                "query": "How do I reset my password?",
                "channel": "email",
                "description": "Password reset question"
            },
            {
                "query": "What's your enterprise pricing?",
                "channel": "web_form",
                "description": "Pricing inquiry (should escalate)"
            },
            {
                "query": "Can you help me with API authentication?",
                "channel": "web_form",
                "description": "Technical question"
            },
            {
                "query": "This is terrible! Fix your broken API!",
                "channel": "email",
                "description": "Angry customer (should escalate)"
            },
            {
                "query": "How do I add team members?",
                "channel": "whatsapp",
                "description": "Team management question"
            }
        ]

        for i, test_case in enumerate(test_cases, 1):
            print(f"Test {i}: {test_case['description']}")
            print(f"Query: {test_case['query']}")
            print(f"Channel: {test_case['channel']}")

            result = agent.process_customer_query(test_case['query'], test_case['channel'])

            print(f"Sentiment: {result['sentiment_score']:.2f}")
            print(f"Escalated: {result['escalated']}")
            if result['escalated']:
                print(f"Reason: {result['escalation_reason']}")
            print(f"Response: {result['response'][:100]}...")
            print("-" * 50)

    elif args.interactive:
        print("Customer Success AI Agent - Interactive Mode")
        print("Type 'quit' to exit, 'test' to run test cases\n")

        while True:
            query = input("Customer Query: ").strip()

            if query.lower() == 'quit':
                break
            elif query.lower() == 'test':
                # Run a quick test
                test_result = agent.process_customer_query("How do I reset my password?", "email")
                print(f"Test response: {test_result['response'][:100]}...")
                continue

            if not query:
                continue

            channel = input("Channel (email/whatsapp/web_form) [web_form]: ").strip() or "web_form"

            result = agent.process_customer_query(query, channel)

            print(f"\nResponse: {result['response']}")
            print(f"Sentiment: {result['sentiment_score']:.2f}")
            if result['escalated']:
                print(f"Escalated: {result['escalation_reason']}")
            print("-" * 50)

    else:
        print("Customer Success AI Agent Prototype")
        print("Use --test to run test cases or --interactive to start interactive mode")


if __name__ == "__main__":
    main()
