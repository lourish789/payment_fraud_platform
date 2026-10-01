"""The Claude tool loop, exercised against a scripted client (no network): parallel tool calls, report
validation with feedback, and append-only history."""

from types import SimpleNamespace as NS

import pytest

from payguard.agent.runner import ClaudeProvider
from payguard.agent.tools import InvestigationTools
from payguard.api.app import build_container
from payguard.api.security import create_client
from payguard.config import Settings
from tests.helpers import REPO, build_tiny_model, fraud_like


def msg(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, model="claude-opus-5",
              usage=NS(input_tokens=1000, output_tokens=200))


def tool(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def case(tmp_path):
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'c.db').as_posix()}", artifacts_dir=tmp_path / "a",
                 rules_path=REPO / "configs" / "rules.yaml")
    build_tiny_model(s.models_dir)
    c = build_container(s, start_workers=False)
    cid = create_client(c.session_factory, "m", "merchant")[0]
    for i in range(60):
        r = c.scoring.score(fraud_like(i, amount=2500.0), cid)
        if r.case_id:
            tools = InvestigationTools(c.session_factory, c.explainer)
            return tools, tools.context(r.case_id)
    raise AssertionError("nothing flagged")


def test_claude_loop_parallel_tools_rejects_bad_report_then_accepts(case):
    tools, ctx = case
    report = dict(recommendation="fraud", confidence=0.8, summary="Shared device with confirmed fraud.",
                  next_action="confirm_decline")
    client = ScriptedClient([
        msg(NS(type="text", text="Looking."), tool("t1", "get_case_overview"), tool("t2", "get_linked_entities")),
        msg(tool("t3", "submit_report", **report, evidence=[{"claim": "made up", "tool_call_id": "t99"}])),
        msg(tool("t4", "submit_report", **report, evidence=[{"claim": "Case overview", "tool_call_id": "t1"}])),
    ])
    res = ClaudeProvider("claude-opus-5", client=client).investigate(tools, ctx)

    assert res.error is None and res.report.recommendation == "fraud"
    assert [t["id"] for t in res.trace] == ["t1", "t2"]
    assert res.grounding["citation_valid_rate"] == 1.0
    assert (res.input_tokens, res.output_tokens) == (3000, 600)
    # both parallel tool results went back in ONE user message
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "user" and [b["tool_use_id"] for b in second[-1]["content"]] == ["t1", "t2"]
    # the invalid report was rejected with an error result the model can act on
    third = client.requests[2]["messages"][-1]["content"][0]
    assert third["is_error"] and "unknown tool_call_ids" in third["content"]
    # history is append-only: every request extends the previous one
    for a, b in zip(client.requests, client.requests[1:]):
        assert b["messages"][:len(a["messages"])] == a["messages"]
    req = client.requests[0]
    assert req["thinking"] == {"type": "adaptive"} and req["fallbacks"] == "default"


def test_claude_loop_handles_refusal_and_step_limit(case):
    tools, ctx = case
    refused = ClaudeProvider("claude-opus-5", client=ScriptedClient([msg(stop="refusal")])).investigate(tools, ctx)
    assert refused.report is None and "declined" in refused.error
    looping = ScriptedClient([msg(tool(f"t{i}", "get_case_overview")) for i in range(3)])
    res = ClaudeProvider("claude-opus-5", max_steps=3, client=looping).investigate(tools, ctx)
    assert res.report is None and "no report after 3 steps" in res.error
