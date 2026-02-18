from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import List, Optional
import uuid

from src.services.database import get_db
from src.services.metrics_service import metrics_service

router = APIRouter()


class LearningMetricsResponse(BaseModel):
    total_successful_qa_pairs: int
    reused_qa_pairs: int
    total_feedback_submitted: int
    high_rated_feedback: int
    learning_effectiveness_ratio: float


class QAPairItem(BaseModel):
    question: str
    answer: str
    rating: int
    times_reused: int
    category: Optional[str] = None


class LowRatedQuestionItem(BaseModel):
    feedback_id: str
    question: str
    ticket_subject: str
    category: str
    rating: int
    comment: Optional[str] = None


class DailyMetricsItem(BaseModel):
    date: str
    feedback_count: int
    qa_pairs_created: int
    avg_rating: float


class TopReusedQAPairsResponse(BaseModel):
    top_qa_pairs: List[QAPairItem]


class LowRatedQuestionsResponse(BaseModel):
    low_rated_questions: List[LowRatedQuestionItem]


class DailyPerformanceResponse(BaseModel):
    daily_metrics: List[DailyMetricsItem]


@router.get("/learning", response_model=LearningMetricsResponse)
async def get_learning_metrics(db: AsyncSession = Depends(get_db)):
    """
    Get metrics related to the learning system.
    """
    metrics = await metrics_service.get_learning_metrics(db)
    return LearningMetricsResponse(**metrics)


@router.get("/learning/top-reused", response_model=TopReusedQAPairsResponse)
async def get_top_reused_qa_pairs(db: AsyncSession = Depends(get_db)):
    """
    Get the top Q&A pairs that have been most frequently reused.
    """
    qa_pairs = await metrics_service.get_top_reused_qa_pairs(db)
    return TopReusedQAPairsResponse(top_qa_pairs=qa_pairs)


@router.get("/learning/low-rated", response_model=LowRatedQuestionsResponse)
async def get_low_rated_questions(db: AsyncSession = Depends(get_db)):
    """
    Get questions that received low ratings (need improvement).
    """
    questions = await metrics_service.get_low_rated_questions(db)
    return LowRatedQuestionsResponse(low_rated_questions=questions)


@router.get("/learning/daily-performance", response_model=DailyPerformanceResponse)
async def get_daily_performance_metrics(db: AsyncSession = Depends(get_db)):
    """
    Get daily performance metrics for the last 7 days.
    """
    metrics = await metrics_service.get_daily_performance_metrics(db)
    return DailyPerformanceResponse(daily_metrics=metrics)