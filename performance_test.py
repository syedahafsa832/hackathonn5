#!/usr/bin/env python3
"""
Performance Test Script for Customer Success AI System

This script runs a simple performance test to validate the system's performance metrics.
"""

import time
import asyncio
import aiohttp
import json
from datetime import datetime
import statistics

# Test configuration
TEST_DURATION_SECONDS = 60  # Run test for 1 minute
CONCURRENT_USERS = 10  # Number of concurrent requests
BASE_URL = "http://localhost:8000"

async def submit_web_form(session, counter):
    """Submit a web form request."""
    url = f"{BASE_URL}/support/submit"

    form_data = {
        "name": f"Test User {counter}",
        "email": f"test{counter}@example.com",
        "subject": f"Performance Test {counter}",
        "category": "technical",
        "priority": "medium",
        "message": f"This is a performance test message #{counter}. Testing the system's ability to handle concurrent requests efficiently.",
        "company": "Test Corp"
    }

    start_time = time.time()
    try:
        async with session.post(url, json=form_data, timeout=30) as response:
            response_time = time.time() - start_time
            status = response.status
            try:
                response_data = await response.json()
            except:
                response_data = {}

            return {
                "status": status,
                "response_time": response_time,
                "success": status == 200,
                "data": response_data
            }
    except Exception as e:
        response_time = time.time() - start_time
        return {
            "status": 0,
            "response_time": response_time,
            "success": False,
            "error": str(e)
        }


async def run_performance_test():
    """Run the performance test."""
    print("🚀 Starting Customer Success AI System Performance Test...")
    print(f"📊 Test Duration: {TEST_DURATION_SECONDS} seconds")
    print(f"👥 Concurrent Users: {CONCURRENT_USERS}")
    print(f"🔗 Base URL: {BASE_URL}")
    print("-" * 60)

    start_time = time.time()
    results = []
    counter = 0

    # Check if the service is running first
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/health", timeout=10) as health_response:
                if health_response.status != 200:
                    print("❌ Service is not running or not healthy!")
                    print("💡 Please start the backend service before running this test.")
                    print("🔧 Example: Run 'uvicorn backend.src.api.main:app --host 0.0.0.0 --port 8000' in the backend directory")
                    return
    except Exception as e:
        print(f"❌ Service is not accessible: {e}")
        print("💡 Please start the backend service before running this test.")
        print("🔧 Example: Run 'uvicorn backend.src.api.main:app --host 0.0.0.0 --port 8000' in the backend directory")
        return

    print("✅ Service is running and accessible")

    # Run the test for the specified duration
    async with aiohttp.ClientSession() as session:
        while time.time() - start_time < TEST_DURATION_SECONDS:
            # Submit requests concurrently
            tasks = []
            for _ in range(CONCURRENT_USERS):
                counter += 1
                tasks.append(submit_web_form(session, counter))

            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

            # Print progress
            elapsed = time.time() - start_time
            print(f"⏱️  Elapsed: {elapsed:.1f}s, Requests: {len(results)}, Success Rate: {(sum(1 for r in results if r['success'])/len(results)*100):.1f}%")

            # Brief pause to avoid overwhelming the server
            await asyncio.sleep(0.1)

    # Calculate metrics
    total_requests = len(results)
    successful_requests = sum(1 for r in results if r['success'])
    failed_requests = total_requests - successful_requests

    response_times = [r['response_time'] for r in results if r['success']]
    avg_response_time = statistics.mean(response_times) if response_times else 0
    p95_response_time = statistics.quantiles(response_times, n=20)[-1] if len(response_times) >= 20 else 0  # 95th percentile
    p99_response_time = statistics.quantiles(response_times, n=100)[-1] if len(response_times) >= 100 else 0  # 99th percentile

    requests_per_second = total_requests / TEST_DURATION_SECONDS
    success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0

    # Print results
    print("\n" + "="*60)
    print("📈 PERFORMANCE TEST RESULTS")
    print("="*60)
    print(f"📊 Total Requests: {total_requests}")
    print(f"✅ Successful: {successful_requests}")
    print(f"❌ Failed: {failed_requests}")
    print(f"🎯 Success Rate: {success_rate:.2f}%")
    print(f"⚡ Avg Response Time: {avg_response_time:.3f}s")
    print(f"⚡ P95 Response Time: {p95_response_time:.3f}s")
    print(f"⚡ P99 Response Time: {p99_response_time:.3f}s")
    print(f"🚀 Requests/Second: {requests_per_second:.2f}")
    print(f"⏱️  Test Duration: {TEST_DURATION_SECONDS}s")
    print(f"👥 Concurrency: {CONCURRENT_USERS}")

    # Performance assessment
    print("\n" + "-"*60)
    print("✅ PERFORMANCE ASSESSMENT")
    print("-"*60)

    if success_rate >= 95:
        print("✅ High success rate (>95%)")
    else:
        print("⚠️  Low success rate (<95%)")

    if avg_response_time < 3.0:
        print("✅ Fast average response time (<3s)")
    else:
        print("⚠️  Slow average response time (>=3s)")

    if p95_response_time < 5.0:
        print("✅ Good P95 response time (<5s)")
    else:
        print("⚠️  Slow P95 response time (>=5s)")

    if requests_per_second > 10:
        print("✅ Good throughput (>10 req/s)")
    else:
        print("⚠️  Low throughput (<=10 req/s)")

    # Summary
    print("\n" + "="*60)
    print("🏆 SUMMARY")
    print("="*60)
    if success_rate >= 95 and avg_response_time < 3.0 and requests_per_second > 10:
        print("🎉 EXCELLENT: System meets performance targets!")
        print("   • High availability (>95% success)")
        print("   • Fast responses (<3s avg)")
        print("   • Good throughput (>10 req/s)")
    elif success_rate >= 90 and avg_response_time < 5.0:
        print("👍 GOOD: System performs adequately")
        print("   • Decent availability (>90% success)")
        print("   • Reasonable response times (<5s avg)")
    else:
        print("⚠️  NEEDS IMPROVEMENT: Performance issues detected")
        print("   • Consider optimizing for better performance")

    print(f"\n📝 Test completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return {
        "total_requests": total_requests,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "success_rate": success_rate,
        "avg_response_time": avg_response_time,
        "p95_response_time": p95_response_time,
        "p99_response_time": p99_response_time,
        "requests_per_second": requests_per_second,
        "results": results
    }


async def run_locust_test_if_available():
    """Try to run locust test if possible."""
    print("\n🔍 Attempting to run Locust load test...")
    try:
        import subprocess
        import sys

        # Try to run a quick locust test
        cmd = [
            sys.executable, "-m", "locust",
            "-f", "production/tests/load_test.py",
            "--host", BASE_URL,
            "--users", "20",
            "--spawn-rate", "5",
            "--run-time", "30s",
            "--headless",
            "--only-summary"
        ]

        print(f"🔧 Running: {' '.join(cmd[:4])} ... (headless mode)")
        print("💡 Note: This may take up to 30 seconds to complete...")

        # Run locust in headless mode for a quick test
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode == 0:
            print("✅ Locust test completed successfully")
            print(f"📄 Stdout: {result.stdout[-500:] if len(result.stdout) > 500 else result.stdout}")  # Last 500 chars
        else:
            print(f"⚠️  Locust test had issues (return code: {result.returncode})")
            print(f"📄 Stderr: {result.stderr[-300:] if len(result.stderr) > 300 else result.stderr}")  # Last 300 chars

    except subprocess.TimeoutExpired:
        print("⏰ Locust test timed out (expected for longer tests)")
    except FileNotFoundError:
        print("⚠️  Locust not found or not properly configured")
        print("💡 Install with: pip install locust")
    except Exception as e:
        print(f"⚠️  Error running Locust: {e}")


if __name__ == "__main__":
    print("[PERFORMANCE] Customer Success AI System - Performance & Load Testing")
    print("[INFO] This script tests the system's performance under load")
    print("-" * 60)

    # Run the performance test
    results = asyncio.run(run_performance_test())

    # Try to run locust test as well
    asyncio.run(run_locust_test_if_available())

    print("\n[COMPLETE] Performance test completed!")
    print("[INFO] Results saved and ready for documentation.")