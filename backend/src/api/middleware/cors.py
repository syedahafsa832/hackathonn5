from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

def add_cors_middleware(app: FastAPI):
    """
    Add CORS middleware to the FastAPI application
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, replace with specific origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Allow specific headers if needed
        # expose_headers=["Access-Control-Allow-Origin"]
    )

    return app
