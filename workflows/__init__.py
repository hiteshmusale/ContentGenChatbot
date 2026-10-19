from workflows import runners
from workflows.runners import WorkflowContext, WorkflowError

WORKFLOWS = {
    "generate_email": runners.generate_email,
    "adapt_email": runners.adapt_email,
    "personalize_email": runners.personalize_email,
}

__all__ = ["WORKFLOWS", "WorkflowContext", "WorkflowError"]
