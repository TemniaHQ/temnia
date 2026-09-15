"""What a stopped topic run tells its reader, derived from the failing activity."""

from __future__ import annotations

from typing import Literal

from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

ACTIVITY_RETRY = RetryPolicy(maximum_attempts=3)


def failure_cause(error: BaseException) -> BaseException:
    """Preserve paid-outcome and budget semantics across activity and child boundaries."""
    while isinstance(error, (ActivityError, ChildWorkflowError)) and error.cause is not None:
        error = error.cause
    return error


_MODEL_STAGE_LABELS = (
    ("topic_opportunity_inventory", "source opportunity inventory"),
    ("topic_selection_author", "author"),
    ("topic_selection_cold", "cold review"),
    ("topic_selection_source", "source review"),
    ("topic_selection_patch", "repair"),
    ("editorial", "editorial"),
    ("proposal", "proposal"),
    ("summary", "summary"),
)


def _failing_stage(error: Exception) -> str:
    """Name the stage from the failing activity; agent names carry it across Temporal."""
    activity_type = str(getattr(error, "activity_type", "") or "")
    for marker, label in _MODEL_STAGE_LABELS:
        if marker in activity_type:
            return label
    return "model"


def _cause_message(cause: BaseException | None) -> str:
    """Read the exact sentence the raising site wrote, across the Temporal boundary."""
    if cause is None:
        return ""
    message = cause.message if isinstance(cause, ApplicationError) else str(cause)
    return message.strip()


RECONCILED_RUN_MESSAGE = (
    "A provider call ended without a confirmed outcome; its charge was reconciled from the "
    "gateway receipt. Retry this run to resume from its retained work."
)


def outcome_unknown(error: Exception) -> bool:
    """The run is fenced on an unconfirmed provider outcome."""
    cause = failure_cause(error)
    error_type = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return error_type == "OutcomeUnknown"


def known_failure_details(  # noqa: C901, PLR0911
    error: Exception,
) -> tuple[Literal["failed", "budget_paused"], str]:
    """Map a stopped activity to the run status and the sentence its reader gets."""
    cause = failure_cause(error)
    error_type = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    if error_type == "BudgetExceeded":
        return "budget_paused", "The run budget cannot cover the next qualified operation."
    if error_type == "DispatchLimitExceeded":
        return "failed", "The run reached its configured physical dispatch limit."
    if error_type == "KnownProviderRejection":
        rejection = _cause_message(cause) or "A route rejected the request."
        if "(HTTP 402)" in rejection:
            return "failed", (
                f"{rejection} The gateway account has insufficient credits or the API key has "
                "a spending limit; top up or raise the limit, then start a new run. Nothing "
                "was charged or retried."
            )
        return "failed", (
            f"{rejection} Change the route snapshot and start a new run; nothing was retried."
        )
    if error_type == "ContextWindowExceeded":
        return "failed", (
            _cause_message(cause) or "The request exceeds the route's context window."
        )
    if error_type == "UnexpectedModelBehavior":
        return "failed", (
            f"The {_failing_stage(error)} response was incomplete or invalid; "
            "the charge is retained and nothing was retried."
        )
    if error_type == "NoEligibleRoute":
        detail = _cause_message(cause) or "No route is eligible for one editorial seat."
        return "failed", (
            f"{detail} Choose different author and reviewer models, or change the route "
            "snapshot; nothing was charged for the refused request."
        )
    if error_type == "DecisionRoutesExhausted":
        detail = _cause_message(cause) or "Every eligible route failed for one decision."
        return "failed", (
            f"{detail} Retry this run later or choose different models; settled work and "
            "charges are retained and reused."
        )
    if error_type == "SeatRoutesExhausted":
        exhausted = _cause_message(cause) or "Every qualified route for one seat failed."
        return "failed", (
            f"{exhausted} Transient failures received a retry after a pause; incompatible "
            "requests moved to the next eligible route. Retry this run later or change the "
            "route snapshot. Settled work and charges are retained."
        )
    if error_type == "TransientProviderFailure":
        settled = _cause_message(cause) or "A provider call ended without a response."
        return "failed", (
            f"{settled} Three attempts ended the same way; nothing more is retried. "
            "Change the route snapshot or start a new run later."
        )
    return "failed", f"The run stopped after an activity failure: {error_type}."
