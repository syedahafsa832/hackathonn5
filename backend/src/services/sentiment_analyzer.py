from textblob import TextBlob
import math
from typing import Union, Tuple

class SentimentAnalyzer:
    """
    Analyzes sentiment of text content using TextBlob
    Returns a sentiment score between -1.0 (very negative) and 1.0 (very positive)
    """

    def __init__(self):
        pass

    def analyze_sentiment(self, text: str) -> float:
        """
        Analyze sentiment of the given text

        Args:
            text (str): Text to analyze

        Returns:
            float: Sentiment score between -1.0 and 1.0
        """
        if not text or not isinstance(text, str):
            return 0.0  # Neutral if no text or invalid input

        # Use TextBlob to analyze sentiment
        blob = TextBlob(text)

        # Get polarity score (between -1 and 1)
        polarity = blob.sentiment.polarity

        # Ensure the value is within the expected range
        return max(-1.0, min(1.0, polarity))

    def is_negative_sentiment(self, text: str, threshold: float = 0.3) -> bool:
        """
        Check if the sentiment is negative based on the threshold

        Args:
            text (str): Text to analyze
            threshold (float): Threshold for negative sentiment (default 0.3)

        Returns:
            bool: True if sentiment is negative, False otherwise
        """
        sentiment_score = self.analyze_sentiment(text)
        return sentiment_score < -threshold

    def analyze_sentiment_detailed(self, text: str) -> dict:
        """
        Analyze sentiment with more details

        Args:
            text (str): Text to analyze

        Returns:
            dict: Detailed sentiment analysis
        """
        if not text or not isinstance(text, str):
            return {
                'score': 0.0,
                'label': 'neutral',
                'confidence': 0.0,
                'raw_text': text[:100] + '...' if len(text) > 100 else text
            }

        blob = TextBlob(text)
        polarity = max(-1.0, min(1.0, blob.sentiment.polarity))
        subjectivity = blob.sentiment.subjectivity

        # Determine sentiment label
        if polarity > 0.1:
            label = 'positive'
        elif polarity < -0.1:
            label = 'negative'
        else:
            label = 'neutral'

        return {
            'score': polarity,
            'label': label,
            'subjectivity': subjectivity,
            'confidence': abs(polarity),
            'raw_text': text[:100] + '...' if len(text) > 100 else text
        }


# Global instance
sentiment_analyzer = SentimentAnalyzer()
