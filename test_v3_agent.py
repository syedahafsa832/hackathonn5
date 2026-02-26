import asyncio
import os
import json
import sys

# Setup Path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from src.agent.customer_success_agent import customer_success_agent

async def test_v3_capabilities():
    print("Starting V3 AI Agent Verification (Aurelio & Finch)\n")
    
    customer_info = {
        "name": "Syeda Hafsa",
        "history": "Frequent shopper, interested in sustainable fabrics.",
        "height": 170,
        "weight": 65
    }

    test_queries = [
        "What is the Aurelio & Finch brand philosophy?",
        "I am 170cm and 65kg, what size do you recommend for a slim fit shirt?",
        "What is your return policy for international orders?",
        "I am very angry that my order hasn't arrived yet! This is unacceptable."
    ]

    for query in test_queries:
        print(f"--- Customer Query: {query} ---")
        try:
            response = await customer_success_agent.process_customer_query(query, customer_info)
            print(f"Intent: {response.get('intent')}")
            print(f"Sentiment: {response.get('sentiment')}")
            print(f"Confidence: {response.get('confidence_score')}%")
            print(f"Status: {response.get('status')}")
            print(f"Reply:\n{response.get('reply_body')}")
            print("-" * 50 + "\n")
        except Exception as e:
            print(f"❌ Error during test: {e}\n")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(test_v3_capabilities())
