from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid

from ..models.ticket import Ticket
from ..models.ticket_feedback import TicketFeedback, SuccessfulQAPair
from ..models.message import Message


class MetricsService:
    """Service for tracking and reporting system metrics."""

    @staticmethod
    async def get_learning_metrics(db: AsyncSession) -> Dict[str, int]:
        """
        Get metrics related to the learning system.
        """
        # Count of successful Q&A pairs stored
        qa_count_result = await db.execute(
            select(func.count(SuccessfulQAPair.id))
            .where(SuccessfulQAPair.is_active == True)
        )
        successful_qa_count = qa_count_result.scalar() or 0

        # Count of Q&A pairs that have been reused
        reused_result = await db.execute(
            select(func.count(SuccessfulQAPair.id))
            .where(SuccessfulQAPair.times_reused > 0)
        )
        reused_qa_count = reused_result.scalar() or 0

        # Count of feedback records
        feedback_count_result = await db.execute(
            select(func.count(TicketFeedback.id))
        )
        feedback_count = feedback_count_result.scalar() or 0

        # Count of high-rated feedback (4-5 stars)
        high_rated_result = await db.execute(
            select(func.count(TicketFeedback.id))
            .where(TicketFeedback.customer_rating >= 4)
        )
        high_rated_count = high_rated_result.scalar() or 0

        return {
            "total_successful_qa_pairs": successful_qa_count,
            "reused_qa_pairs": reused_qa_count,
            "total_feedback_submitted": feedback_count,
            "high_rated_feedback": high_rated_count,
            "learning_effectiveness_ratio": high_rated_count / feedback_count if feedback_count > 0 else 0
        }

    @staticmethod
    async def get_top_reused_qa_pairs(db: AsyncSession, limit: int = 10) -> List[Dict]:
        """
        Get the top Q&A pairs that have been most frequently reused.
        """
        result = await db.execute(
            select(
                SuccessfulQAPair.original_question,
                SuccessfulQAPair.ai_response,
                SuccessfulQAPair.customer_rating,
                SuccessfulQAPair.times_reused,
                SuccessfulQAPair.category
            )
            .where(SuccessfulQAPair.times_reused > 0)
            .order_by(SuccessfulQAPair.times_reused.desc())
            .limit(limit)
        )

        rows = result.all()
        return [
            {
                "question": row.original_question,
                "answer": row.ai_response,
                "rating": row.customer_rating,
                "times_reused": row.times_reused,
                "category": row.category
            }
            for row in rows
        ]

    @staticmethod
    async def get_low_rated_questions(db: AsyncSession, limit: int = 10) -> List[Dict]:
        """
        Get questions that received low ratings (need improvement).
        """
        result = await db.execute(
            select(
                TicketFeedback.id,
                TicketFeedback.feedback_comment,
                TicketFeedback.customer_rating,
                Ticket.subject.label('ticket_subject'),
                Ticket.category,
                Message.content.label('original_question')
            )
            .join(Ticket, TicketFeedback.ticket_id == Ticket.id)
            .join(Message, Ticket.conversation_id == Message.conversation_id)
            .where(TicketFeedback.customer_rating <= 2)
            .where(Message.direction == 'inbound')  # The customer's question
            .order_by(TicketFeedback.created_at.desc())
            .limit(limit)
        )

        rows = result.all()
        return [
            {
                "feedback_id": str(row.id),
                "question": row.original_question,
                "ticket_subject": row.ticket_subject,
                "category": row.category,
                "rating": row.customer_rating,
                "comment": row.feedback_comment
            }
            for row in rows
        ]

    @staticmethod
    async def get_daily_performance_metrics(db: AsyncSession, days: int = 7) -> List[Dict]:
        """
        Get daily performance metrics for the last N days.
        """
        start_date = datetime.utcnow() - timedelta(days=days)

        # Get daily counts of tickets, feedback, and QA pairs
        result = await db.execute(text("""
            SELECT
                DATE(tf.created_at) as date,
                COUNT(tf.id) as feedback_count,
                COUNT(sq.id) as qa_pairs_created,
                AVG(tf.customer_rating) as avg_rating
            FROM ticket_feedback tf
            LEFT JOIN successful_qa_pairs sq ON DATE(sq.created_at) = DATE(tf.created_at)
            WHERE tf.created_at >= :start_date
            GROUP BY DATE(tf.created_at)
            ORDER BY DATE(tf.created_at) DESC
        """), {"start_date": start_date})

        rows = result.fetchall()
        return [
            {
                "date": row.date.isoformat() if row.date else "",
                "feedback_count": row.feedback_count or 0,
                "qa_pairs_created": row.qa_pairs_created or 0,
                "avg_rating": float(row.avg_rating) if row.avg_rating else 0.0
            }
            for row in rows
        ]

    @staticmethod
    async def track_qa_pair_usage(db: AsyncSession, qa_pair_id: uuid.UUID) -> bool:
        """
        Increment the usage count for a Q&A pair.
        """
        try:
            result = await db.execute(
                select(SuccessfulQAPair).where(SuccessfulQAPair.id == qa_pair_id)
            )
            qa_pair = result.scalar_one_or_none()

            if qa_pair:
                qa_pair.times_reused = (qa_pair.times_reused or 0) + 1
                qa_pair.last_used_at = datetime.utcnow()
                await db.flush()
                return True
            return False
        except Exception:
            # If there's an error, just continue without tracking
            return False


# Global instance
metrics_service = MetricsService()