import asyncio
import json
from backend.src.services.kafka_client import kafka_client_service

async def test_kafka_connectivity():
    print("Testing Kafka connectivity...")
    test_topic = "fte.test.connectivity"
    test_message = {"test": "data", "status": "success"}
    
    try:
        # Test Producer
        print("Testing Producer connection...")
        kafka_client_service.start_producer()
        print("Publishing test message...")
        await kafka_client_service.publish_message(test_topic, test_message)
        print("Message published successfully!")
        
        # Shutdown
        kafka_client_service.shutdown()
        print("Kafka connectivity test passed!")
        
    except Exception as e:
        print(f"Kafka connectivity test failed: {str(e)}")
        exit(1)

if __name__ == "__main__":
    asyncio.run(test_kafka_connectivity())
