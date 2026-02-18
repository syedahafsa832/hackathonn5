from locust import HttpUser, task, between, SequentialTaskSet
import random
import json
from datetime import datetime


class WebFormUser(HttpUser):
    """
    Simulates users submitting support forms
    """
    wait_time = between(2, 10)
    weight = 3  # Higher weight means more frequent use

    def on_start(self):
        """Called when a user starts"""
        self.categories = ["technical", "billing", "sales", "general"]
        self.priorities = ["low", "medium", "high", "critical"]

    @task
    def submit_support_form(self):
        """Submit random support form"""
        # Generate random support request
        support_data = {
            "name": f"Load Test User {random.randint(1000, 9999)}",
            "email": f"loadtest{random.randint(1000, 9999)}@example.com",
            "subject": random.choice([
                "API Integration Help",
                "Billing Issue",
                "Feature Request",
                "Login Problem",
                "Documentation Question",
                "Performance Issue"
            ]),
            "category": random.choice(self.categories),
            "priority": random.choice(self.priorities),
            "message": random.choice([
                "I'm having trouble integrating with your API. Can you provide more detailed documentation?",
                "My billing statement shows charges I didn't authorize. Please investigate.",
                "I'd like to request a new feature for bulk user management.",
                "I can't log in to my account. The password reset isn't working.",
                "I have a question about the rate limiting for API calls.",
                "The application is running slowly and timing out frequently."
            ])
        }

        headers = {'Content-Type': 'application/json'}
        response = self.client.post(
            "/support/submit",
            data=json.dumps(support_data),
            headers=headers,
            name="/support/submit"
        )

        # Log response for analysis
        if response.status_code != 200:
            print(f"Error submitting form: {response.status_code} - {response.text}")


class HealthCheckUser(HttpUser):
    """
    Simulates health checks and monitoring requests
    """
    wait_time = between(5, 15)
    weight = 1  # Lower weight means less frequent use

    @task
    def check_health(self):
        """Check health endpoint"""
        response = self.client.get("/health", name="/health")
        if response.status_code != 200:
            print(f"Health check failed: {response.status_code}")


class ApiUser(HttpUser):
    """
    Simulates API users making various requests
    """
    wait_time = between(3, 12)
    weight = 2

    def on_start(self):
        """Initialize user session"""
        # We'll use a dummy ticket ID for testing
        self.ticket_ids = [
            f"ticket-{i}" for i in range(1, 100)
        ]

    @task(3)
    def submit_support_request(self):
        """Submit support request via API"""
        data = {
            "name": f"API User {random.randint(1, 1000)}",
            "email": f"apiuser{random.randint(1, 1000)}@test.com",
            "subject": "API Usage Question",
            "category": "technical",
            "priority": random.choice(["low", "medium"]),
            "message": "I have a question about using your API efficiently."
        }
        headers = {'Content-Type': 'application/json'}
        self.client.post(
            "/support/submit",
            data=json.dumps(data),
            headers=headers,
            name="/support/submit"
        )

    @task(1)
    def check_ticket_status(self):
        """Check status of a random ticket"""
        ticket_id = random.choice(self.ticket_ids)
        self.client.get(f"/support/ticket/{ticket_id}", name="/support/ticket/{id}")


class WebhookUser(HttpUser):
    """
    Simulates webhook traffic from external services
    """
    wait_time = between(1, 8)
    weight = 1

    @task
    def simulate_gmail_webhook(self):
        """Simulate Gmail webhook notification"""
        # Simulate a simplified Gmail webhook payload
        payload = {
            "userId": "me",
            "historyId": str(random.randint(1000000, 9999999)),
            "messages": [{"id": f"msg-{random.randint(1000, 9999)}"}]
        }
        headers = {'Content-Type': 'application/json'}
        self.client.post(
            "/webhooks/gmail",
            data=json.dumps(payload),
            headers=headers,
            name="/webhooks/gmail"
        )

    @task
    def simulate_whatsapp_webhook(self):
        """Simulate WhatsApp webhook notification"""
        # Simulate a simplified WhatsApp webhook payload
        form_data = {
            "From": f"whatsapp:+1{random.randint(1000000000, 9999999999)}",
            "To": "whatsapp:+1234567890",
            "Body": random.choice([
                "Help with account",
                "Question about pricing",
                "API integration issue",
                "Login problem"
            ]),
            "MessageSid": f"SM{random.randint(1000000000000000, 9999999999999999)}",
            "NumMedia": "0"
        }
        # Note: WhatsApp webhooks typically use form-encoded data
        self.client.post(
            "/webhooks/whatsapp",
            data=form_data,
            name="/webhooks/whatsapp"
        )


class ScenarioUser(HttpUser):
    """
    User that performs a complete scenario
    """
    wait_time = between(5, 15)
    weight = 2

    def on_start(self):
        """Initialize scenario user"""
        self.user_id = f"user_{random.randint(1000, 9999)}"
        self.email = f"{self.user_id}@example.com"

    @task
    def complete_scenario(self):
        """Complete a full support scenario"""
        # Submit a support request
        support_data = {
            "name": self.user_id.replace("user_", "Scenario User "),
            "email": self.email,
            "subject": "Complete Scenario Test",
            "category": random.choice(["technical", "billing", "general"]),
            "priority": random.choice(["low", "medium"]),
            "message": f"Scenario test from {self.user_id} at {datetime.now().isoformat()}"
        }

        headers = {'Content-Type': 'application/json'}
        response = self.client.post(
            "/support/submit",
            data=json.dumps(support_data),
            headers=headers,
            name="scenario/submit"
        )

        # If successful, maybe check status later
        if response.status_code == 200:
            try:
                response_data = response.json()
                ticket_id = response_data.get('id')
                if ticket_id:
                    # Simulate checking status later
                    self.client.get(f"/support/ticket/{ticket_id}", name="scenario/check-status")
            except:
                pass  # Ignore if response isn't JSON


class HeavyLoadUser(HttpUser):
    """
    User that generates heavy load for stress testing
    """
    wait_time = between(0.5, 2)
    weight = 5  # Highest weight for heavy load

    @task
    def submit_multiple_requests(self):
        """Submit multiple requests quickly"""
        for i in range(random.randint(1, 3)):
            data = {
                "name": f"Heavy Load User {random.randint(10000, 99999)}",
                "email": f"heavy{random.randint(10000, 99999)}@stress.com",
                "subject": f"Stress Test {i}",
                "category": random.choice(["technical", "general"]),
                "priority": "medium",
                "message": f"Stress testing request #{i} at {datetime.now().isoformat()}"
            }
            headers = {'Content-Type': 'application/json'}
            try:
                self.client.post(
                    "/support/submit",
                    data=json.dumps(data),
                    headers=headers,
                    name="heavy-load/submit"
                )
            except:
                pass  # Continue even if some requests fail

    @task(2)
    def health_check_frequent(self):
        """Frequent health checks during heavy load"""
        try:
            self.client.get("/health", name="heavy-load/health")
        except:
            pass  # Continue even if health check fails


# Define different user behaviors for various load patterns
class LoadTestShape:
    """
    Custom load shape for gradual load increase
    """
    def tick(self):
        run_time = self.get_run_time()

        if run_time < 60:  # Ramp up for first minute
            user_count = int(run_time / 60 * 100)  # Go from 0 to 100 users
            spawn_rate = 5
        elif run_time < 300:  # Hold at 100 users for 4 minutes
            user_count = 100
            spawn_rate = 10
        elif run_time < 360:  # Spike to 200 users for 1 minute
            user_count = 200
            spawn_rate = 20
        elif run_time < 420:  # Drop to 50 users for 1 minute
            user_count = 50
            spawn_rate = 10
        else:  # Stop test after 7 minutes
            return None

        return (user_count, spawn_rate)
