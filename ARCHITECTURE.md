# Customer Success AI Agent - Architecture Document

## Overview

This document provides a deep technical dive into the architecture of the Customer Success AI Agent system. The system is designed as an event-driven, scalable platform that handles customer inquiries across multiple communication channels with intelligent response generation and escalation capabilities.

## System Architecture

### High-Level Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   WhatsApp      │    │   Web Form       │    │   Email         │
│   Channel       │    │   Channel        │    │   Channel       │
└─────────┬───────┘    └─────────┬────────┘    └─────────┬───────┘
          │                      │                       │
          │                      │                       │
          ▼                      ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API Gateway Layer                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐  │
│  │  Web Form   │  │ WhatsApp    │  │ Email Simulator    │  │
│  │  Handler    │  │  Handler    │  │  Handler           │  │
│  └─────────────┘  └─────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Kafka Message Queue                         │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Topic: fte.tickets.incoming                           │  │
│  │  Topic: fte.whatsapp.incoming                          │  │
│  │  Topic: fte.conversations.escalated                    │  │
│  │  Topic: fte.metrics                                    │  │
│  │  Topic: fte.dlq                                        │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                Message Processing Workers                     │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  UnifiedMessageProcessor                               │  │
│  │  - Customer Identification                            │  │
│  │  - Conversation Management                            │  │
│  │  - AI Agent Integration                               │  │
│  │  - Response Generation                                │  │
│  │  - Channel-specific Formatting                        │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Database Layer                             │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL with pgvector                            │  │
│  │  - customers table                                   │  │
│  │  - conversations table                               │  │
│  │  - messages table                                    │  │
│  │  - tickets table                                     │  │
│  │  - knowledge_base table                              │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AI & Services Layer                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Mistral AI     │  │  Knowledge      │  │  Sentiment      │ │
│  │  Integration    │  │  Base Service   │  │  Analyzer       │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Component Architecture

### 1. Channel Handlers

#### WhatsApp Handler (`src/services/whatsapp_handler.py`)
- **Purpose**: Process incoming WhatsApp messages
- **Responsibilities**:
  - Webhook validation
  - Message parsing
  - Response formatting (≤300 characters)
  - Twilio API integration
- **Communication**: Kafka `fte.whatsapp.incoming` topic
- **Technology**: Twilio SDK, FastAPI webhook endpoints

#### Web Form Handler (`src/api/routes/support.py`)
- **Purpose**: Handle web form submissions
- **Responsibilities**:
  - Form validation
  - Ticket creation
  - Customer identification
  - Kafka message publishing
- **Communication**: Kafka `fte.tickets.incoming` topic
- **Technology**: FastAPI, Pydantic validation

#### Email Handler (`src/api/routes/email.py`)
- **Purpose**: Handle email submissions (simulated for hackathon)
- **Responsibilities**:
  - Email data validation
  - Formal response formatting
  - Customer identification
  - Kafka message publishing
- **Communication**: Kafka `fte.tickets.incoming` topic
- **Technology**: FastAPI, Pydantic validation

### 2. Message Processing Engine

#### Unified Message Processor (`production/workers/message_processor.py`)
- **Purpose**: Centralized message processing
- **Responsibilities**:
  - Cross-channel customer identification
  - Conversation management
  - AI agent orchestration
  - Response routing
  - Metrics collection
- **Technology**: Async Python, Kafka Consumer
- **Pattern**: Event-driven, reactive processing

### 3. AI Agent Core

#### Customer Success Agent (`src/agent/customer_success_agent.py`)
- **Purpose**: Generate intelligent customer responses
- **Responsibilities**:
  - Context-aware response generation
  - Escalation detection
  - Sentiment analysis integration
  - Channel-appropriate formatting
- **Technology**: Mistral AI, LangChain-like patterns
- **Pattern**: Stateful conversation context

### 4. Data Layer

#### Database Schema
- **PostgreSQL** with extensions:
  - `pgvector` for knowledge base similarity search
  - JSONB for flexible data storage
  - UUID primary keys for distributed systems

##### Tables:
- `customers`: Customer profiles with cross-channel identifiers
- `conversations`: Cross-channel conversation threads
- `messages`: Individual message records with metadata
- `tickets`: Support ticket tracking
- `knowledge_base`: FAQ and documentation articles

### 5. Communication Layer

#### Kafka Topics
- `fte.tickets.incoming`: New tickets from all channels
- `fte.whatsapp.incoming`: WhatsApp-specific messages
- `fte.conversations.escalated`: Escalation notifications
- `fte.metrics`: Performance metrics
- `fte.dlq`: Dead letter queue for failed messages
- `fte.whatsapp.outbound`: WhatsApp response routing
- `fte.webform.outbound`: Web form response routing
- `fte.email.outbound`: Email response routing

## Database Schema

### Customers Table
```sql
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(50) UNIQUE,
    name VARCHAR(255),
    company VARCHAR(255),
    tier VARCHAR(50), -- 'free', 'pro', 'enterprise'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Conversations Table
```sql
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id),
    initial_channel VARCHAR(50), -- 'whatsapp', 'web_form', 'email'
    status VARCHAR(50), -- 'open', 'in_progress', 'escalated', 'resolved', 'closed'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Messages Table
```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    channel VARCHAR(50), -- 'whatsapp', 'web_form', 'email'
    direction VARCHAR(10), -- 'inbound', 'outbound'
    sender_identifier VARCHAR(255),
    content TEXT,
    sentiment_score DECIMAL(3,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Tickets Table
```sql
CREATE TABLE tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id),
    conversation_id UUID REFERENCES conversations(id),
    source_channel VARCHAR(50), -- 'whatsapp', 'web_form', 'email'
    category VARCHAR(100), -- 'technical', 'billing', 'sales', 'general'
    priority VARCHAR(50), -- 'low', 'medium', 'high', 'critical'
    status VARCHAR(50), -- 'open', 'in_progress', 'escalated', 'resolved', 'closed'
    subject VARCHAR(255),
    description TEXT,
    assigned_agent VARCHAR(255),
    resolution_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE
);
```

## Scalability Considerations

### Horizontal Scaling
- **API Layer**: Stateless FastAPI services, easily scaled
- **Worker Layer**: Multiple message processors, parallel processing
- **Database**: Connection pooling, read replicas
- **Kafka**: Partitioned topics for parallel processing

### Auto-Scaling Configuration
- **API Pods**: 3-20 replicas based on CPU (70% threshold)
- **Worker Pods**: 3-30 replicas based on CPU (70% threshold)
- **Database**: Connection limits (20 max) with pooling

### Performance Optimizations
- **Connection Pooling**: SQLAlchemy async connection pools
- **Caching**: Redis for frequently accessed data
- **Indexing**: Strategic database indexing
- **Async Processing**: Non-blocking I/O operations

## Security Architecture

### Data Protection
- **Encryption**: AES-256 at rest, TLS 1.3 in transit
- **PII Handling**: Minimal data collection, secure storage
- **Access Controls**: Role-based authentication
- **Audit Logging**: Comprehensive activity tracking

### API Security
- **Rate Limiting**: Per-user and global limits
- **Authentication**: Token-based for internal services
- **Input Validation**: Comprehensive validation at all layers
- **Injection Prevention**: Parameterized queries, sanitized inputs

## Resilience Patterns

### Circuit Breaker
- **Implementation**: In AI service calls
- **Purpose**: Prevent cascade failures
- **Recovery**: Exponential backoff

### Retry Logic
- **Implementation**: Kafka producer/consumer
- **Strategy**: Exponential backoff with jitter
- **Limits**: Maximum retry attempts

### Dead Letter Queue
- **Purpose**: Failed message handling
- **Implementation**: Kafka topic for failed messages
- **Processing**: Manual review and reprocessing

## Monitoring and Observability

### Logging
- **Structured Logging**: JSON format with correlation IDs
- **Levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Storage**: Centralized logging system ready

### Metrics
- **Application**: Response times, error rates, throughput
- **Business**: Customer satisfaction, resolution times
- **Infrastructure**: CPU, memory, disk usage

### Tracing
- **Distributed**: Request tracing across services
- **Correlation**: Request IDs across service boundaries
- **Performance**: Bottleneck identification

## Channel-Specific Architecture

### WhatsApp Channel
- **Real-time**: Immediate response requirement
- **Constraints**: 300-character limit, media handling
- **Format**: Casual, brief responses with emojis
- **Integration**: Meta WhatsApp Business API via Twilio

### Web Form Channel
- **Structured**: Form-based input validation
- **Format**: Semi-formal, helpful responses
- **Tracking**: Ticket ID generation and status
- **Integration**: Direct API submission

### Email Channel
- **Formal**: Professional, detailed responses
- **Format**: Email template with greeting/signature
- **Threading**: Conversation context preservation
- **Integration**: Gmail API (production) / Simulator (hackathon)

## Cross-Channel Continuity

### Customer Identification
- **Multiple Identifiers**: Email, phone, customer ID
- **Fuzzy Matching**: Similar name/email detection
- **Privacy Compliance**: GDPR/CCPA compliant
- **Linking**: Automatic association of identities

### Conversation History
- **Unified Timeline**: Chronological message ordering
- **Channel Preservation**: Original channel context
- **Context Transfer**: Relevant information across channels
- **Continuity**: Seamless experience switching channels

## AI Decision Logic

### Escalation Triggers
- **Keywords**: Pricing, legal, manager, supervisor
- **Sentiment**: Negative sentiment scores (< -0.5)
- **Complexity**: Multi-part questions requiring human judgment
- **Anger Detection**: Profanity and aggressive language

### Response Generation
- **Context Awareness**: Customer history and conversation context
- **Knowledge Base**: Semantic search for relevant articles
- **Channel Appropriateness**: Tone and format adaptation
- **Confidence Scoring**: Uncertainty detection and escalation

## Deployment Architecture

### Kubernetes Deployment
- **Namespace**: Isolated environment (customer-success-fte)
- **Deployments**: API and worker services with replica management
- **Services**: Internal and external service discovery
- **Ingress**: HTTPS routing with TLS termination

### Configuration Management
- **ConfigMaps**: Non-sensitive configuration
- **Secrets**: Encrypted sensitive data (API keys, passwords)
- **Environment**: Runtime configuration via environment variables
- **Rollouts**: Zero-downtime deployments

### Resource Management
- **Requests/Limits**: CPU and memory constraints per pod
- **Health Checks**: Liveness and readiness probes
- **Auto-Scaling**: HPA based on CPU utilization
- **Resource Quotas**: Namespace-level resource limits

## Future Enhancements

### Planned Features
- **Advanced Analytics**: Predictive customer satisfaction
- **Multi-Language**: Internationalization support
- **Voice Channel**: Voice call integration
- **Mobile App**: Native mobile application

### Architecture Evolution
- **Microservices**: Further service decomposition
- **Event Sourcing**: Complete event-driven architecture
- **CQRS**: Command Query Responsibility Segregation
- **Domain Driven Design**: Bounded contexts

## Technology Stack

### Backend
- **Language**: Python 3.9+
- **Framework**: FastAPI
- **Database**: PostgreSQL 14+
- **Message Queue**: Apache Kafka
- **AI Provider**: Mistral AI

### Infrastructure
- **Containerization**: Docker
- **Orchestration**: Kubernetes
- **Load Balancing**: NGINX Ingress
- **Monitoring**: Prometheus/Grafana (ready for integration)

### Frontend
- **Framework**: React 18+
- **Styling**: Tailwind CSS
- **State Management**: React Hooks/Context
- **API Client**: Axios

This architecture provides a solid foundation for a scalable, maintainable, and production-ready customer success AI system that can handle enterprise-level loads while providing exceptional customer experience across multiple channels.