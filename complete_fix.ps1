Write-Host "`n🔧 Complete System Fix`n" -ForegroundColor Cyan

# Step 1: Stop everything
Write-Host "Step 1: Stopping services..." -ForegroundColor Yellow
docker-compose down
Write-Host "✅ Stopped`n"

# Step 2: Start services
Write-Host "Step 2: Starting services..." -ForegroundColor Yellow
docker-compose up -d
Write-Host "Waiting for startup..."
Start-Sleep -Seconds 20
Write-Host "✅ Started`n"

# Step 3: Create Kafka topics
Write-Host "Step 3: Creating Kafka topics..." -ForegroundColor Yellow
$topics = @(
    "fte.tickets.incoming",
    "fte.whatsapp.incoming", 
    "fte.escalations",
    "webform_outbound",
    "fte.dlq"
)

foreach ($topic in $topics) {
    docker exec customer-success-fte-kafka kafka-topics --create --if-not-exists --topic $topic --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1 2>$null
}
Write-Host "✅ Topics created`n"

# Step 4: Fix database
Write-Host "Step 4: Fixing database..." -ForegroundColor Yellow
$sql = @"
ALTER TABLE conversations ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE conversations ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE messages ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE customers ALTER COLUMN created_at SET DEFAULT NOW();
DELETE FROM messages;
DELETE FROM conversations;
DELETE FROM tickets;
DELETE FROM customers;
"@

$sql | docker exec -i customer-success-fte-postgres psql -U postgres -d fte_db
Write-Host "✅ Database fixed`n"

# Step 5: Restart services to load new env
Write-Host "Step 5: Restarting services..." -ForegroundColor Yellow
docker-compose restart backend worker
Start-Sleep -Seconds 10
Write-Host "✅ Restarted`n"

# Step 6: Test
Write-Host "Step 6: Testing web form..." -ForegroundColor Yellow
$body = @{
    name = "Fix Test"
    email = "fixtest@example.com"
    subject = "Test After Fix"
    category = "technical"
    message = "Testing if the fix worked!"
    priority = "medium"
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/support/submit" -Method Post -Body $body -ContentType "application/json"
    Write-Host "✅ Web form works!" -ForegroundColor Green
    Write-Host "   Ticket: $($response.ticket_id)`n"
} catch {
    Write-Host "❌ Still having issues" -ForegroundColor Red
    Write-Host "   Error: $($_.Exception.Message)`n"
}

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Next: Watch worker logs and check database`n"
Write-Host "Terminal 1: docker logs -f customer-success-fte-worker"
Write-Host "Terminal 2: docker exec -it customer-success-fte-postgres psql -U postgres -d fte_db -c 'SELECT * FROM messages;'`n"
Write-Host "================================`n" -ForegroundColor Cyan
