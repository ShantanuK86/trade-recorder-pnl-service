"""Reconciliation endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ReconcileRequest, ReconcileResponse
from app.services.reconciliation_service import reconcile

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/", response_model=ReconcileResponse)
def run_reconciliation(
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
):
    """
    Compare stored trades for a venue against an external statement.

    Returns missing, extra, and mismatched trades.
    """
    return reconcile(db, payload)

