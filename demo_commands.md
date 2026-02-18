# Demo Commands for Judges

## 1. Show All Running Docker Containers

```bash
docker-compose ps
```

**Expected Output**: Shows all 10 containers running (postgres, redis, kafka, zookeeper, api, worker, learning-worker, email-poller, web-form)

---

## 2. Show Docker Container Health Status

```bash
docker-compose ps --format "table {{.Name}}\t{{.State}}\t{{.Status}}"
```

**Shows**: Container names, running state, and health status

---

## 3. Verify API is Healthy

```bash
curl http://localhost:8000/health
```

**Expected Output**: `{"status":"healthy","timestamp":"...","service":"customer-success-agent-api"}`

---

## 4. Show Database Tables

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "\dt"
```

**Shows**: All database tables (customers, conversations, messages, tickets, customer_identifiers, etc.)

---

## 5. Show Customer Data

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT id, name, email, phone, created_at FROM customers ORDER BY created_at DESC LIMIT 5;"
```

**Shows**: Recent customers with their contact information

---

## 6. Show Conversations

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT id, customer_id, initial_channel, status, created_at FROM conversations ORDER BY created_at DESC LIMIT 5;"
```

**Shows**: Recent conversations with channel and status

---

## 7. Show Messages

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT id, conversation_id, channel, direction, content, created_at FROM messages ORDER BY created_at DESC LIMIT 5;"
```

**Shows**: Recent messages (inbound/outbound) with content

---

## 8. Show Tickets

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT id, customer_id, status, priority, channel, subject, created_at FROM tickets ORDER BY created_at DESC LIMIT 5;"
```

**Shows**: Recent support tickets with status and priority

---

## 9. Show Cross-Channel Customer Identification

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT c.id, c.name, c.email, c.phone, STRING_AGG(DISTINCT conv.initial_channel, ', ') as channels FROM customers c LEFT JOIN conversations conv ON c.id = conv.customer_id GROUP BY c.id, c.name, c.email, c.phone LIMIT 5;"
```

**Shows**: Customers with all channels they've used (demonstrates cross-channel identification)

---

## 10. Show Database Statistics

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT 'Customers' as table_name, COUNT(*) as count FROM customers UNION ALL SELECT 'Conversations', COUNT(*) FROM conversations UNION ALL SELECT 'Messages', COUNT(*) FROM messages UNION ALL SELECT 'Tickets', COUNT(*) FROM tickets;"
```

**Shows**: Total counts for all main tables

---

## 11. Show Worker Logs (Processing Messages)

```bash
docker-compose logs worker --tail=20
```

**Shows**: Recent message processing activity

---

## 12. Show Email Poller Logs

```bash
docker-compose logs email_poller --tail=20
```

**Shows**: Email polling and processing activity

---

## 13. Show All Services Logs

```bash
docker-compose logs --tail=10
```

**Shows**: Recent logs from all services

---

## 14. Show Docker Images

```bash
docker images | grep hack5
```

**Shows**: All built Docker images for the project

---

## 15. Show Database Connection Info

```bash
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT version();"
```

**Shows**: PostgreSQL version and confirms database connectivity

---

## Quick Demo Script

Run all key commands in sequence:

```bash
# Show all containers
echo "=== Docker Containers ==="
docker-compose ps

# Show API health
echo -e "\n=== API Health Check ==="
curl http://localhost:8000/health

# Show database tables
echo -e "\n=== Database Tables ==="
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "\dt"

# Show customer count
echo -e "\n=== Database Statistics ==="
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT 'Customers' as table_name, COUNT(*) as count FROM customers UNION ALL SELECT 'Conversations', COUNT(*) FROM conversations UNION ALL SELECT 'Messages', COUNT(*) FROM messages UNION ALL SELECT 'Tickets', COUNT(*) FROM tickets;"

# Show recent activity
echo -e "\n=== Recent Messages ==="
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT id, channel, direction, LEFT(content, 50) as content_preview, created_at FROM messages ORDER BY created_at DESC LIMIT 3;"
```

---

## PowerShell Version (for Windows)

```powershell
# Show all containers
Write-Host "=== Docker Containers ===" -ForegroundColor Green
docker-compose ps

# Show API health
Write-Host "`n=== API Health Check ===" -ForegroundColor Green
Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing | Select-Object -ExpandProperty Content

# Show database tables
Write-Host "`n=== Database Tables ===" -ForegroundColor Green
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "\dt"

# Show statistics
Write-Host "`n=== Database Statistics ===" -ForegroundColor Green
docker exec -it customer-success-fte-postgres psql -U postgres -d customer_success -c "SELECT 'Customers' as table_name, COUNT(*) as count FROM customers UNION ALL SELECT 'Conversations', COUNT(*) FROM conversations UNION ALL SELECT 'Messages', COUNT(*) FROM messages UNION ALL SELECT 'Tickets', COUNT(*) FROM tickets;"
```
