from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_evaluation_proposal_service
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.contracts.proposals import (
    EvaluationProposalV1,
    EvaluationProposalValidationV1,
)

router = APIRouter(prefix="/api/v1/evaluation-proposals", tags=["evaluation-proposals"])
Service = Annotated[EvaluationProposalService, Depends(get_evaluation_proposal_service)]


@router.post("/validate", response_model=EvaluationProposalValidationV1)
def validate_proposal(payload: EvaluationProposalV1, service: Service):
    return service.validate(payload)
