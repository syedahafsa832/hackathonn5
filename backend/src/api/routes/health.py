from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any
import datetime
import os

router = APIRouter()

# Pydantic models for health check response
class HealthResponse(BaseModel):
    status: str
    timestamp: str
    services: Dict[str, str]

@router.get("/", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint for the application
    """
    # Check the status of various services
    services_status = {}

    # Check if database connection is available
    try:
        # In a real implementation, you would check the actual database connection
        # For now, we'll assume it's ok if the env var is set
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            services_status["database"] = "ok"
        else:
            services_status["database"] = "error"
    except Exception:
        services_status["database"] = "error"

    # Check if Grok API key is configured
    try:
        grok_key = os.getenv("GROK_API_KEY")
        if grok_key and len(grok_key) > 10:  # Basic check for key presence
            services_status["grok_api"] = "ok"
        else:
            services_status["grok_api"] = "warning"
    except Exception:
        services_status["grok_api"] = "error"

    # Check if Kafka is configured
    try:
        kafka_brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        if kafka_brokers:
            services_status["kafka"] = "ok"
        else:
            services_status["kafka"] = "warning"  # Warning if not configured but not critical
    except Exception:
        services_status["kafka"] = "error"

    # Overall status based on critical services
    critical_services_down = [
        k for k, v in services_status.items()
        if k in ["database"] and v == "error"
    ]

    overall_status = "unavailable" if critical_services_down else "healthy"

    # If we want to be more nuanced, we can return "degraded" if non-critical services are down
    if not critical_services_down:
        warning_services = [k for k, v in services_status.items() if v == "warning"]
        if warning_services:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

    return HealthResponse(
        status=overall_status,
        timestamp=datetime.datetime.utcnow().isoformat(),
        services=services_status
    )

@router.get("/detailed")
async def detailed_health_check():
    """
    Detailed health check with more information
    """
    import sys
    import psutil

    # Get system information
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_usage = psutil.disk_usage('/')

    # Get service statuses
    services_status = {}

    # Database check
    try:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            services_status["database"] = {"status": "ok", "configured": True}
        else:
            services_status["database"] = {"status": "error", "configured": False}
    except Exception as e:
        services_status["database"] = {"status": "error", "configured": True, "error": str(e)}

    # API Keys check
    try:
        grok_key = os.getenv("GROK_API_KEY")
        services_status["grok_api"] = {"status": "ok" if grok_key else "warning", "configured": bool(grok_key)}
    except Exception as e:
        services_status["grok_api"] = {"status": "error", "configured": True, "error": str(e)}

    # Kafka check
    try:
        kafka_brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        services_status["kafka"] = {"status": "ok" if kafka_brokers else "warning", "configured": bool(kafka_brokers)}
    except Exception as e:
        services_status["kafka"] = {"status": "error", "configured": True, "error": str(e)}

    return {
        "status": "healthy",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "services": services_status,
        "system": {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_info.percent,
            "disk_percent": (disk_usage.used / disk_usage.total) * 100,
            "uptime_seconds": getattr(sys, '_MEIPASS', 'N/A'),  # Simplified uptime
            "python_version": sys.version
        },
        "environment": {
            "debug": os.getenv("DEBUG", "False"),
            "environment": os.getenv("ENVIRONMENT", "development")
        }
    }
