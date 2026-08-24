"""Request and response models for the debugging API.

These are the contract. FastAPI uses them to parse and validate incoming JSON,
to serialise responses, and to generate the docs at /docs -- so a field
description here is also the documentation a client reads.

The important shape decision: POST /debug and POST /sessions/{id}/approve
return the SAME model. Both can end in one of two places -- paused at the
approval interrupt, or finished -- so a caller always branches on `status`
rather than on which endpoint it called.
"""

from enum import Enum
from typing import Literal , Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.tools.linter import SAFE_REPOSITORY_ID

class SessionStatus(str, Enum):
    """Where a debugging session currently sits.

    Inherits from str as well as Enum so it serialises to a plain JSON string
    ("completed") instead of an object, which keeps clients simple.
    """
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"

# REPO UPLOAD

class UploadResponse(BaseModel):
    repository_id: str = Field(
        ...,
        description = "Identifier to pass to /debug for this repo.",
        examples=["my_repo"],
    )
    files_loaded: int = Field(..., description= "Source files read from the archive")
    chunks_indexed: int = Field(..., description = "Chunks written to the vector store.")
    message: str = Field(..., description= "Human-readable summary of what happened")

class RepositorySummary(BaseModel):
    """one uploaded reposiotry, as listed GET /repositories."""

    repository_id: str = Field(
        ...,
        description="Pass this to /debug.",
        examples=["my_repo"],
    )
    chunks_indexed: int = Field(
        ...,
        description="Chunks in the vector store. zero means it needs re-uploading"
    )
    

    
# STARTING DEBUGGING SESSION

class DebugRequest(BaseModel):
    repository_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="A repository already uploaded via /repositories/upload.",
        examples=["my_repo"],
    )
    issue_description: str = Field(
        ...,
        min_length=1,
        max_length=20_000,
        description="The bug report, error message, or stack trace to investigate.",
        examples=["test_add fails: add(2, 3) returns -1 instead of 5."],
    )

    @field_validator("repository_id")
    @classmethod

    def _validate_repository_id(cls, v:str) -> str:
        # Same rule the tools enforce, applied at the boundary. A bad id becomes
        # a clean 422 here instead of an UnsafePathError surfacing from deep
        # inside a graph node.
        if not SAFE_REPOSITORY_ID.match(v):
            raise ValueError(
                "repository_id may contain only letters,digits, _ and -"
            )
        return v
    

    @field_validator("issue_description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        #min length =1 alone would let through " ".
        if not v.strip():
            raise ValueError("issue description cannot be blank")
        return v


#
## HUMAN APPROVAL STEP
#


class ApprovalRequest(BaseModel):
    """What a reviewer sends back for a paused session.

    Mirrors the three-way decision the graph's human_approval node expects.
    Deliberately not a boolean: "revise" is neither approval nor rejection --
    it sends the feedback back to the model for another attempt.
    """

    action: Literal["approve", "reject", "revise"] = Field(
        ...,
        description=(
            "approve = apply the patch. "
            "reject = discard it and end the session. "
            "revise = send `feedback` back to the model and generate a new patch."
        ),
    )

    feedback: str = Field(
        "",
        max_length=10_000,
        description="notes for model. required when action is 'revise'.",
        examples=["Right idea, but you fixed the wrong function"],
    )

    @model_validator(mode="after")

    def _revise_needs_feedback(self) -> "ApprovalRequest":
        # At temperature=0 a revision with nothing to act on regenerates the
        # identical patch, so fail loudly rather than doing a confusing no-op.
        if self.action == "revise" and not self.feedback.strip():
            raise ValueError("feedback is required when action is 'revise'")
        return self

#
## RESPONSES
#

class ReviewPayload(BaseModel):
    """Everything a human needs in order to judge a proposed patch.

    Keys mirror the dict passed to interrupt() in nodes.human_approval.
    All optional -- a session can pause with some of these unset.
    """

    target_file: Optional[str] = Field(None, description="File the patch applies to.")
    patch_type: Optional[str] = Field(None, description="'full' or 'snippet'.")
    patch: Optional[str] = Field(None, description="The proposed change.")
    plan: Optional[str] = Field(None, description="The model's debugging plan.")
    syntax_check_result: Optional[str] = Field(None, description="py_compile output.")
    lint_result: Optional[str] = Field(None, description="ruff output.")
    test_result: Optional[str] = Field(
        None,
        description="pytest verdict, compared against a pre-patch baseline.",
    )


class DebugResponse(BaseModel):
    """Returned by both /debug and /sessions/{id}/approve.

    Branch on `status`: AWAITING_APPROVAL means `review` is populated and the
    session is paused; COMPLETED means `final_report` is populated and it's over.
    """

    session_id: str = Field(
        ...,
        description="Pass this to /sessions/{id}/approve to resume.",
    )
    status: SessionStatus
    review: Optional[ReviewPayload] = Field(
        None,
        description="Present when status is awaiting_approval.",
    )
    final_report: Optional[str] = Field(
        None,
        description="Present when status is completed.",
    )


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="What went wrong.")