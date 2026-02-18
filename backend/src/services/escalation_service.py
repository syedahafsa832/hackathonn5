import re
from typing import Dict, Any, List
from enum import Enum
import logging

from .sentiment_analyzer import sentiment_analyzer

logger = logging.getLogger(__name__)

class EscalationReason(Enum):
    PRICING = "pricing_inquiry"
    LEGAL = "legal_matter"
    REFUND = "refund_request"
    NEGATIVE_SENTIMENT = "negative_sentiment"
    PROFANITY = "profanity_detected"
    ANGRY_CUSTOMER = "angry_customer"
    COMPLEX_ISSUE = "complex_technical_issue"
    OTHER = "other"

class EscalationService:
    def __init__(self):
        # Keywords that trigger escalation
        self.pricing_keywords = [
            'price', 'pricing', 'cost', 'costs', 'pay', 'payment', 'payments',
            'bill', 'billing', 'charged', 'charges', 'expensive', 'cheaper',
            'discount', 'deal', 'offer', 'subscription', 'plan', 'plans'
        ]

        self.legal_keywords = [
            'legal', 'lawyer', 'law', 'attorney', 'contract', 'agreement',
            'terms', 'condition', 'liability', 'responsibility', 'obligation',
            'breach', 'violation', 'lawsuit', 'court', 'judge', 'regulation',
            'compliance', 'policy_violation'
        ]

        self.refund_keywords = [
            'refund', 'return', 'money_back', 'compensation', 'reimbursement',
            'credit', 'reversal', 'cancel_charge', 'dispute', 'chargeback',
            'unsatisfied', 'unhappy_with_purchase'
        ]

        self.angry_keywords = [
            'angry', 'frustrated', 'disappointed', 'annoyed', 'mad',
            'upset', 'furious', 'livid', 'pissed', 'irritated'
        ]

        self.profanity_list = [
            'fuck', 'shit', 'damn', 'bitch', 'asshole', 'bastard', 'cunt',
            'dick', 'piss', 'bloody', 'bollocks', 'arsehole', 'bugger',
            'crap', 'damn', 'feck', 'slut', 'whore', 'wanker'
        ]

    def check_escalation_triggers(self, message: str, sentiment_score: float = None) -> Dict[str, Any]:
        """
        Check if a message contains escalation triggers

        Args:
            message (str): The message to check
            sentiment_score (float, optional): Pre-calculated sentiment score

        Returns:
            Dict[str, Any]: Escalation information with reasons and confidence
        """
        reasons = []
        confidence_scores = []

        # Convert to lowercase for comparison
        lower_msg = message.lower()

        # Check for pricing keywords
        pricing_matches = [word for word in self.pricing_keywords if word in lower_msg]
        if pricing_matches:
            reasons.append(EscalationReason.PRICING)
            confidence_scores.append(0.9)  # High confidence for pricing

        # Check for legal keywords
        legal_matches = [word for word in self.legal_keywords if word in lower_msg]
        if legal_matches:
            reasons.append(EscalationReason.LEGAL)
            confidence_scores.append(0.95)  # Very high confidence for legal

        # Check for refund keywords
        refund_matches = [word for word in self.refund_keywords if word in lower_msg]
        if refund_matches:
            reasons.append(EscalationReason.REFUND)
            confidence_scores.append(0.85)  # High confidence for refunds

        # Analyze sentiment if not provided
        if sentiment_score is None:
            sentiment_score = sentiment_analyzer.analyze_sentiment(message)

        # Check for negative sentiment
        if sentiment_score < -0.3:  # Negative sentiment threshold
            reasons.append(EscalationReason.NEGATIVE_SENTIMENT)
            # Confidence increases with negativity
            confidence_scores.append(min(0.9, abs(sentiment_score)))

        # Check for profanity
        profanity_matches = [word for word in self.profanity_list if word in lower_msg]
        if profanity_matches:
            reasons.append(EscalationReason.PROFANITY)
            confidence_scores.append(0.95)  # High confidence for profanity

        # Check for angry customer indicators
        angry_matches = [word for word in self.angry_keywords if word in lower_msg]
        if angry_matches:
            reasons.append(EscalationReason.ANGRY_CUSTOMER)
            confidence_scores.append(0.8)  # Medium-high confidence

        # Calculate overall escalation probability
        if reasons:
            avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
            return {
                "should_escalate": True,
                "reasons": [reason.value for reason in reasons],
                "confidence": avg_confidence,
                "details": {
                    "pricing_keywords_found": pricing_matches,
                    "legal_keywords_found": legal_matches,
                    "refund_keywords_found": refund_matches,
                    "profanity_found": profanity_matches,
                    "angry_keywords_found": angry_matches,
                    "sentiment_score": sentiment_score
                }
            }
        else:
            return {
                "should_escalate": False,
                "reasons": [],
                "confidence": 0,
                "details": {
                    "sentiment_score": sentiment_score
                }
            }

    def should_escalate(self, message: str, sentiment_score: float = None,
                       min_confidence: float = 0.5) -> bool:
        """
        Determine if a message should be escalated based on triggers

        Args:
            message (str): The message to check
            sentiment_score (float, optional): Pre-calculated sentiment score
            min_confidence (float): Minimum confidence threshold for escalation

        Returns:
            bool: Whether the message should be escalated
        """
        escalation_info = self.check_escalation_triggers(message, sentiment_score)

        if escalation_info["should_escalate"]:
            return escalation_info["confidence"] >= min_confidence
        return False

    def get_escalation_reason(self, message: str) -> List[EscalationReason]:
        """
        Get specific reasons why a message should be escalated

        Args:
            message (str): The message to check

        Returns:
            List[EscalationReason]: List of escalation reasons
        """
        escalation_info = self.check_escalation_triggers(message)
        return [EscalationReason(reason) for reason in escalation_info["reasons"]]

    def create_escalation_payload(self, message: str, customer_id: str,
                                conversation_id: str, channel: str) -> Dict[str, Any]:
        """
        Create a payload for escalation to human agent

        Args:
            message (str): The original message
            customer_id (str): Customer identifier
            conversation_id (str): Conversation identifier
            channel (str): Communication channel

        Returns:
            Dict[str, Any]: Escalation payload
        """
        escalation_info = self.check_escalation_triggers(message)

        return {
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "channel": channel,
            "original_message": message,
            "escalation_reasons": escalation_info["reasons"],
            "confidence_score": escalation_info["confidence"],
            "analysis_details": escalation_info["details"],
            "escalated_at": __import__('datetime').datetime.utcnow().isoformat(),
            "triggered_by_ai": True
        }

    def analyze_complexity(self, message: str) -> Dict[str, Any]:
        """
        Analyze message complexity to determine if escalation is needed

        Args:
            message (str): The message to analyze

        Returns:
            Dict[str, Any]: Complexity analysis
        """
        # Count technical terms, question marks, exclamation marks
        words = message.split()
        technical_indicators = [
            'api', 'integration', 'sdk', 'webhook', 'endpoint', 'authentication',
            'oauth', 'configuration', 'setup', 'installation', 'debug', 'error',
            'exception', 'crash', 'bug', 'performance', 'latency', 'throughput'
        ]

        technical_matches = [word for word in words if word.lower() in technical_indicators]
        question_marks = message.count('?')
        exclamation_marks = message.count('!')
        word_count = len(words)

        # Calculate complexity score
        complexity_score = 0
        if technical_matches:
            complexity_score += 0.3 * len(technical_matches) / len(technical_indicators)
        if question_marks > 2:
            complexity_score += 0.2
        if exclamation_marks > 3:
            complexity_score += 0.2
        if word_count > 100:  # Long, potentially complex message
            complexity_score += 0.3

        complexity_score = min(1.0, complexity_score)  # Cap at 1.0

        return {
            "complexity_score": complexity_score,
            "technical_terms": technical_matches,
            "question_count": question_marks,
            "exclamation_count": exclamation_marks,
            "word_count": word_count,
            "should_escalate": complexity_score > 0.5
        }


# Global instance
escalation_service = EscalationService()
