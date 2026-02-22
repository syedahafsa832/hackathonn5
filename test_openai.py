import os
import logging
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_openai_init():
    api_key = "test_key"
    try:
        logger.info("Testing OpenAI client initialization...")
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.mistral.ai/v1"
        )
        logger.info("✓ OpenAI client initialized.")
    except Exception as e:
        logger.error(f"OpenAI Init Failed: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    test_openai_init()
