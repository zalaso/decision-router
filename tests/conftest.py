import logging

import pytest

from decision_router.audit import JsonAudit
from decision_router.domain import Boolean, Candidate, Choice, DecisionRequest, Score


@pytest.fixture
def request_data():
    return DecisionRequest(
        prompt="Write Python code",
        questions=[
            Choice(
                id="route",
                instructions="Choose an agent",
                candidates=[
                    Candidate(id="coder", description="Write code"),
                    Candidate(id="researcher", description="Search sources"),
                ],
            ),
            Boolean(id="code", instructions="This requires code."),
            Score(id="risk", instructions="Risk level", levels=["low", "medium", "high"]),
        ],
    )


@pytest.fixture
def audit():
    return JsonAudit(logging.getLogger("test.audit"))
