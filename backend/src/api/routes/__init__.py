from fastapi import FastAPI
from . import support, tickets, webhooks

def setup_routes(app: FastAPI):
    """
    Setup all API routes for the application
    """
    app.include_router(support.router, prefix="/support", tags=["support"])
    app.include_router(tickets.router)  # prefix already set in router
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    return app

