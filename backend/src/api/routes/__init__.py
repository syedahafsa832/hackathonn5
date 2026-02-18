from fastapi import FastAPI
from . import support, customers, webhooks, knowledge_base, health, email, ticket_feedback, metrics

def setup_routes(app: FastAPI):
    """
    Setup all API routes for the application
    """
    # Include routers for different modules
    app.include_router(support.router, prefix="/support", tags=["support"])
    app.include_router(customers.router, prefix="/customers", tags=["customers"])
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    app.include_router(knowledge_base.router, prefix="/knowledge-base", tags=["knowledge-base"])
    app.include_router(email.router, prefix="/email", tags=["email"])
    app.include_router(ticket_feedback.router, prefix="/ticket-feedback", tags=["ticket-feedback"])
    app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

    # Health check is added directly to main app
    return app
