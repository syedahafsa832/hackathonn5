import asyncio
import json
import uuid
from typing import Dict, Any
from kafka import KafkaProducer, KafkaConsumer
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class KafkaJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            if isinstance(obj, uuid.UUID):
                return str(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            # If it's a SQLAlchemy model or something else, try to stringify if it's likely an identifier
            if hasattr(obj, '__str__'):
                return str(obj)
            return super().default(obj)
        except Exception:
            return str(obj)

# Define the required topics
TOPICS = {
    'tickets_incoming': 'fte.tickets.incoming',
    'whatsapp_inbound': 'fte.whatsapp.incoming',
    'webform_inbound': 'fte.tickets.incoming', # Web form uses tickets_incoming usually
    'whatsapp_outbound': 'whatsapp_outbound',
    'webform_outbound': 'webform_outbound',
    'escalations': 'fte.conversations.escalated',
    'metrics': 'fte.metrics',
    'dlq': 'fte.dlq'
}


class FTEKafkaProducer:
    """Enhanced Kafka Producer for FTE operations"""

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        self.producer = None
        self.is_connected = False

    def connect(self):
        """Connect to Kafka producer with retries and exponential backoff"""
        import time
        max_retries = 5
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # Remove hardcoded api_version to allow auto-detection
                self.producer = KafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v, cls=KafkaJSONEncoder).encode('utf-8'),
                    key_serializer=lambda k: str(k).encode('utf-8') if k is not None else None,
                    linger_ms=5,  # Small delay to batch messages
                    retries=3,
                    acks='all'  # Ensure message persistence
                )
                self.is_connected = True
                logger.info(f"Connected to Kafka producer on attempt {attempt + 1}")
                return
            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error("All Kafka connection attempts failed")
                    raise

    async def _wrap_kafka_future(self, kafka_future):
        """Wrap a kafka-python future into an asyncio.Future"""
        loop = asyncio.get_running_loop()
        aio_future = loop.create_future()

        def on_success(record_metadata):
            if not aio_future.done():
                loop.call_soon_threadsafe(aio_future.set_result, record_metadata)

        def on_error(exception):
            if not aio_future.done():
                loop.call_soon_threadsafe(aio_future.set_exception, exception)

        kafka_future.add_callback(on_success)
        kafka_future.add_errback(on_error)

        return await aio_future

    async def publish_message(self, topic: str, message: Dict[str, Any], key: str = None):
        """Publish a message to a Kafka topic"""
        if not self.is_connected:
            self.connect()

        try:
            # Add timestamp and metadata to message
            message_with_metadata = {
                **message,
                "timestamp": datetime.utcnow().isoformat(),
                "topic": topic,
                "source": "fte-agent"
            }

            # Send the message (relying on producer's value_serializer)
            future = self.producer.send(topic, key=key, value=message_with_metadata)

            # Wait for completion using custom wrapper
            result = await self._wrap_kafka_future(future)

            logger.info(f"Message published to topic {topic} at offset {result.offset}")
            return result
        except Exception as e:
            logger.error(f"Failed to publish message to topic {topic}: {str(e)}")
            # Put message in DLQ if publishing fails
            await self._publish_to_dlq(topic, message, str(e))
            raise

    async def _publish_to_dlq(self, original_topic: str, original_message: Dict[str, Any], error: str):
        """Publish failed message to Dead Letter Queue"""
        dlq_message = {
            "original_topic": original_topic,
            "original_message": original_message,
            "error": error,
            "failed_at": datetime.utcnow().isoformat()
        }

        try:
            await self.publish_message(TOPICS['dlq'], dlq_message)
            logger.warning(f"Failed message sent to DLQ from topic {original_topic}")
        except Exception as e:
            logger.error(f"Failed to send message to DLQ: {str(e)}")

    def close(self):
        """Close Kafka producer connection"""
        if self.producer:
            self.producer.close()
            self.is_connected = False


class FTEKafkaConsumer:
    """Enhanced Kafka Consumer for FTE operations"""

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        self.consumer = None
        self.is_connected = False

    def connect(self, topics: Any, group_id: str = "fte-consumer-group"):
        """Connect to Kafka consumer for one or more topics with retries"""
        import time
        if isinstance(topics, str):
            topics = [topics]
            
        max_retries = 5
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                self.consumer = KafkaConsumer(
                    *topics,
                    bootstrap_servers=self.bootstrap_servers,
                    group_id=group_id,
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                    auto_offset_reset='earliest',
                    enable_auto_commit=True,
                    max_poll_records=10,
                    session_timeout_ms=30000,
                    heartbeat_interval_ms=10000
                )
                self.is_connected = True
                logger.info(f"Connected to Kafka consumer for topics: {topics} on attempt {attempt + 1}")
                return
            except Exception as e:
                logger.warning(f"Consumer connection attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error("All Kafka consumer connection attempts failed")
                    raise

    def consume_messages(self, topics: Any = None, group_id: str = "fte-consumer-group"):
        """Consume messages from one or more Kafka topics"""
        if topics and not self.is_connected:
            self.connect(topics, group_id)
        elif not self.is_connected:
            raise ValueError("Consumer not connected and no topics provided")

        try:
            for message in self.consumer:
                # Yield a tuple of (topic, message_value)
                yield message.topic, message.value
        except Exception as e:
            logger.error(f"Failed to consume messages: {str(e)}")
            raise

    def close(self):
        """Close Kafka consumer connection"""
        if self.consumer:
            self.consumer.close()
            self.is_connected = False


class KafkaClientService:
    """Service class to manage both producer and consumer"""

    def __init__(self, bootstrap_servers: str = None):
        self.bootstrap_servers = bootstrap_servers or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self.producer = FTEKafkaProducer(self.bootstrap_servers)
        self.consumer = FTEKafkaConsumer(self.bootstrap_servers)

    async def send_to_topic(self, topic_name: str, message: Dict[str, Any], key: str = None):
        """Send a message to a specific topic"""
        if topic_name in TOPICS:
            topic = TOPICS[topic_name]
        else:
            topic = topic_name  # Assume it's a direct topic name

        return await self.producer.publish_message(topic, message, key)

    async def publish_message(self, topic: str, message: Dict[str, Any], key: str = None):
        """Alias for send_to_topic for backward compatibility"""
        return await self.send_to_topic(topic, message, key)

    def listen_to_topic(self, topic_names: Any, group_id: str = "fte-consumer-group"):
        """Listen to one or more topics"""
        actual_topics = []
        if isinstance(topic_names, str):
            actual_topics.append(TOPICS.get(topic_names, topic_names))
        elif isinstance(topic_names, (list, tuple, set)):
            for name in topic_names:
                if isinstance(name, str):
                    actual_topics.append(TOPICS.get(name, name))
                else:
                    actual_topics.append(str(name))
        else:
            actual_topics.append(str(topic_names))

        return self.consumer.consume_messages(actual_topics, group_id)

    def start_producer(self):
        """Initialize the producer"""
        self.producer.connect()

    def start_consumer(self, topic_names: Any, group_id: str = "fte-consumer-group"):
        """Initialize the consumer for one or more topics"""
        actual_topics = []
        if isinstance(topic_names, str):
            actual_topics.append(TOPICS.get(topic_names, topic_names))
        elif isinstance(topic_names, (list, tuple, set)):
            for name in topic_names:
                if isinstance(name, str):
                    actual_topics.append(TOPICS.get(name, name))
                else:
                    actual_topics.append(str(name))
        else:
            actual_topics.append(str(topic_names))

        self.consumer.connect(actual_topics, group_id)

    def shutdown(self):
        """Shutdown both producer and consumer"""
        self.producer.close()
        self.consumer.close()


# Global instance for the service
kafka_client_service = KafkaClientService()

# Convenience functions for backward compatibility
async def publish_message(topic: str, message: Dict[str, Any]):
    """Legacy function for publishing messages"""
    if topic in TOPICS:
        actual_topic = TOPICS[topic]
    else:
        actual_topic = topic
    return await kafka_client_service.producer.publish_message(actual_topic, message)

def consume_messages(topic: str, group_id: str = "fte-consumer-group"):
    """Legacy function for consuming messages"""
    if topic in TOPICS:
        actual_topic = TOPICS[topic]
    else:
        actual_topic = topic
    return kafka_client_service.consumer.consume_messages(actual_topic, group_id)
