from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

# Pydantic models for request/response validation
class EmailResponse(BaseModel):
    status: str
    message: str
    estimated_resolution: Optional[str] = None


# Remove the simulator endpoint and just keep the router for future real email endpoints
# The real email integration will work through the email polling service