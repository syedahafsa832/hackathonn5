# Data Model: Customer Success AI Agent

**Date**: 2026-02-03
**Feature**: Customer Success AI Agent (Digital FTE)
**Branch**: 001-customer-success-agent

## Overview

This document defines the data model for the Customer Success AI Agent, following the PostgreSQL-based CRM approach. All customer data, interactions, and business logic are managed within the PostgreSQL database as required by the constitution.

## Entity Definitions

### 1. Customers
**Table**: `customers`
**Purpose**: Store unified customer records with personal information and account details

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for customer
- `email` (VARCHAR[255], NOT NULL): Primary email address for customer identification
- `phone` (VARCHAR[50]): Phone number for WhatsApp identification
- `name` (VARCHAR[255]): Customer's full name
- `company` (VARCHAR[255]): Company name (optional)
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of record creation
- `updated_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of last update
- `metadata` (JSONB): Additional customer properties (preferences, account status, etc.)

**Relationships**:
- One-to-many with `customer_identifiers`
- One-to-many with `conversations`
- One-to-many with `tickets`

**Validation**:
- Email must be valid format
- Phone must follow international format if provided
- Name cannot be empty

### 2. Customer Identifiers
**Table**: `customer_identifiers`
**Purpose**: Map various identifiers (emails, phone numbers) to customer records for cross-channel recognition

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for identifier record
- `customer_id` (UUID, Foreign Key): Reference to customers.id
- `identifier_type` (ENUM: 'email', 'phone', 'external_id'): Type of identifier
- `identifier_value` (VARCHAR[255], NOT NULL): Actual identifier value
- `is_primary` (BOOLEAN, DEFAULT FALSE): Whether this is the primary identifier for the customer
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of record creation

**Relationships**:
- Many-to-one with `customers` (customer_id)
- Enforced uniqueness on (identifier_type, identifier_value) to prevent duplicates

**Validation**:
- Identifier value must be unique for the same type
- Customer must exist when creating identifier
- Only one primary identifier per type per customer

### 3. Conversations
**Table**: `conversations`
**Purpose**: Track multi-channel conversations linking related interactions across different communication channels

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for conversation
- `customer_id` (UUID, Foreign Key): Reference to customers.id
- `initial_channel` (ENUM: 'email', 'whatsapp', 'web_form'): Channel where conversation started
- `status` (ENUM: 'open', 'closed', 'escalated', 'pending'): Current status of conversation
- `sentiment_score` (NUMERIC(3,2)): Sentiment score between -1.0 and 1.0
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of conversation start
- `updated_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of last activity
- `metadata` (JSONB): Additional conversation properties

**Relationships**:
- Many-to-one with `customers` (customer_id)
- One-to-many with `messages`
- One-to-many with `tickets` (via conversation_tickets junction table)

**Validation**:
- Customer must exist when creating conversation
- Sentiment score must be between -1.0 and 1.0
- Status must be one of allowed values

### 4. Messages
**Table**: `messages`
**Purpose**: Store all individual messages with metadata about channel, timestamp, and content

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for message
- `conversation_id` (UUID, Foreign Key): Reference to conversations.id
- `channel` (ENUM: 'email', 'whatsapp', 'web_form'): Channel where message originated
- `direction` (ENUM: 'inbound', 'outbound'): Direction of message flow
- `sender_identifier` (VARCHAR[255]): Identifier of sender (email/phone)
- `content` (TEXT, NOT NULL): Message content
- `delivery_status` (ENUM: 'sent', 'delivered', 'failed', 'pending'): Status of message delivery
- `sentiment_score` (NUMERIC(3,2)): Sentiment score for this message
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of message creation
- `metadata` (JSONB): Additional message properties (thread_id for email, message_id for WhatsApp)

**Relationships**:
- Many-to-one with `conversations` (conversation_id)

**Validation**:
- Conversation must exist when creating message
- Content cannot be empty
- Sentiment score must be between -1.0 and 1.0
- Delivery status must be one of allowed values

### 5. Tickets
**Table**: `tickets`
**Purpose**: Manage support ticket lifecycle with status, priority, category, and resolution tracking

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for ticket
- `customer_id` (UUID, Foreign Key): Reference to customers.id
- `conversation_id` (UUID, Foreign Key): Reference to conversations.id (may be null for standalone tickets)
- `source_channel` (ENUM: 'email', 'whatsapp', 'web_form'): Channel where ticket originated
- `category` (VARCHAR[100]): Category of support request
- `priority` (ENUM: 'low', 'medium', 'high', 'critical'): Priority level
- `status` (ENUM: 'open', 'in_progress', 'escalated', 'resolved', 'closed'): Current status
- `subject` (VARCHAR[255]): Subject/title of ticket
- `description` (TEXT): Detailed description of issue
- `assigned_agent` (VARCHAR[255]): Name of assigned human agent (if escalated)
- `resolution_notes` (TEXT): Resolution details
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of ticket creation
- `updated_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of last update
- `resolved_at` (TIMESTAMP): Timestamp of resolution (if resolved)
- `escalation_reason` (VARCHAR[100]): Reason for escalation (if escalated)

**Relationships**:
- Many-to-one with `customers` (customer_id)
- Many-to-one with `conversations` (conversation_id)
- One-to-many with `ticket_events` (tracking ticket lifecycle events)

**Validation**:
- Customer must exist when creating ticket
- Status must be one of allowed values
- Priority must be one of allowed values
- Category must be provided

### 6. Knowledge Base
**Table**: `knowledge_base`
**Purpose**: Contain searchable product documentation with vector embeddings for similarity search

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for knowledge base article
- `title` (VARCHAR[255], NOT NULL): Title of the article
- `content` (TEXT, NOT NULL): Full content of the article
- `category` (VARCHAR[100]): Category of the article
- `tags` (TEXT[]): Array of tags for filtering
- `version` (INTEGER, DEFAULT 1): Version number for content updates
- `is_active` (BOOLEAN, DEFAULT TRUE): Whether article is active and searchable
- `created_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of creation
- `updated_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of last update
- `embedding` (VECTOR[1536]): Vector embedding for similarity search (using pgvector)

**Relationships**:
- One-to-many with `kb_article_views` (tracking article popularity)

**Validation**:
- Title and content cannot be empty
- Embedding must be valid vector format
- Version must be positive integer

### 7. Channel Configurations
**Table**: `channel_configs`
**Purpose**: Store configuration parameters for different communication channels

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for configuration
- `channel_type` (ENUM: 'email', 'whatsapp', 'web_form'): Type of channel
- `config_key` (VARCHAR[100], NOT NULL): Configuration parameter name
- `config_value` (TEXT, NOT NULL): Configuration parameter value
- `is_sensitive` (BOOLEAN, DEFAULT FALSE): Whether config contains sensitive data
- `updated_at` (TIMESTAMP, DEFAULT NOW()): Timestamp of last update

**Relationships**:
- Used for storing API keys, webhook URLs, rate limits, etc.

### 8. Agent Metrics
**Table**: `agent_metrics`
**Purpose**: Store operational metrics and performance data for the AI agent

**Fields**:
- `id` (UUID, Primary Key): Unique identifier for metric entry
- `metric_type` (VARCHAR[100], NOT NULL): Type of metric (response_time, accuracy, etc.)
- `metric_value` (NUMERIC): Value of the metric
- `timestamp` (TIMESTAMP, DEFAULT NOW()): When metric was recorded
- `channel` (ENUM: 'email', 'whatsapp', 'web_form', 'overall'): Channel-specific metrics
- `metadata` (JSONB): Additional context for the metric

## Indexes

### Primary Indexes
- `customers.email` (unique, for fast customer lookup)
- `customer_identifiers.identifier_type` and `identifier_value` (composite, for identifier lookup)
- `conversations.customer_id` (for customer conversation history)
- `messages.conversation_id` and `created_at` (composite, for chronological message retrieval)
- `tickets.customer_id` and `status` (composite, for customer ticket status)
- `knowledge_base.embedding` (vector index, for similarity search)

### Secondary Indexes
- `customers.phone` (for WhatsApp customer lookup)
- `conversations.status` (for filtering conversations by status)
- `messages.channel` and `direction` (composite, for channel-specific analysis)
- `tickets.category` and `priority` (composite, for category-based reporting)
- `knowledge_base.category` and `is_active` (composite, for active category search)

## State Transitions

### Ticket State Transitions
- `open` → `in_progress`: When agent begins working on ticket
- `in_progress` → `escalated`: When escalation criteria are met
- `in_progress` → `resolved`: When issue is resolved
- `escalated` → `resolved`: When human agent resolves escalated issue
- `resolved` → `closed`: After customer confirmation or timeout
- `open` → `closed`: If issue is invalid or duplicate

### Conversation State Transitions
- `open` → `closed`: When conversation is resolved or inactive
- `open` → `escalated`: When escalation criteria are met
- `escalated` → `closed`: After human agent handles escalation

## Constraints

### Data Integrity
- Foreign key constraints to maintain referential integrity
- Check constraints for enum values and value ranges
- Unique constraints to prevent duplicate identifiers
- NOT NULL constraints for required fields

### Business Logic
- Customer identification across channels
- Sentiment score range validation (-1.0 to 1.0)
- Cascade deletion for related records (messages when conversation deleted)
- Automatic timestamp updates for created_at/updated_at fields
