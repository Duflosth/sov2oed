"""sov2oed auto with scripted models: the loop, the tools' limits and both providers' message formats."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from sov2oed import agent

FIXTURES = Path(__file__).parent.parent / "fixtures"
CEDAR = FIXTURES / "sov_us_cedar_isd.xlsx"
PLAN = (FIXTURES / "sov_us_cedar_isd.plan.yaml").read_text(encoding="utf-8")


class Scripted:
    """A model that plays a list of turns: each a list of (tool, input), or a final text."""

    name = "scripted"

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def start(self, system, user):
        self.seen.append(("start", user))

    def step(self, results, note=""):
        self.seen.append((results, note))
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return agent.Reply(turn, [])
        return agent.Reply("", [agent.Call(f"c{i}", name, args) for i, (name, args) in enumerate(turn)])


def test_a_scripted_model_converts_and_writes_the_broker_message(tmp_path):
    llm = Scripted([[("inspect", {})], [("decide", {"plan": PLAN})], [("convert", {})], "Hello,\n1. ..."])
    result = agent.run(CEDAR, tmp_path, llm)
    assert (tmp_path / "location.csv").is_file()
    assert (tmp_path / "broker_message.txt").read_text(encoding="utf-8") == "Hello,\n1. ...\n"
    assert (
        result["decide_calls"] == 1
        and result["locations"] == json.loads((tmp_path / "agent.json").read_text())["locations"]
    )
    convert_result = llm.seen[4][0][0]
    assert "questions_txt" in convert_result[1] and not convert_result[2]


def test_decide_is_refused_after_three_calls_and_bad_calls_are_errors(tmp_path):
    turns = [[("decide", {"plan": PLAN})]] * 3 + [[("decide", {"plan": PLAN}), ("delete", {}), ("inspect", {"x": 1})]]
    llm = Scripted([*turns, [("convert", {})], "done"])
    agent.run(CEDAR, tmp_path, llm)
    fourth = llm.seen[5][0]
    assert "called 3 times" in fourth[0][1]
    assert fourth[1][1] == "no tool delete" and fourth[1][2]
    assert "unknown or missing arguments x" in fourth[2][1] and fourth[2][2]


def test_a_model_that_stops_early_is_nudged_then_stopped(tmp_path):
    llm = Scripted(["I think it is fine.", "Done.", "Really done."])
    with pytest.raises(RuntimeError, match="before convert"):
        agent.run(CEDAR, tmp_path, llm)
    assert "not done" in llm.seen[2][1]


def test_a_plan_error_comes_back_to_the_model(tmp_path):
    llm = Scripted([[("decide", {"plan": "plan: 1\nfile: {}\n"})], [("convert", {})], [("decide", {"plan": PLAN})],
                    [("convert", {})], "ok"])  # fmt: skip
    agent.run(CEDAR, tmp_path, llm)
    first, second = llm.seen[2][0][0], llm.seen[3][0][0]
    assert first[2] and "invalid plan" in first[1]
    assert "no plan yet" in second[1]


def _block(**kw):
    return NS(**kw)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


def test_the_anthropic_adapter_sends_results_resumes_pauses_and_reports_fallbacks(monkeypatch):
    sent = []
    replies = [
        NS(stop_reason="pause_turn", model="m", content=[_block(type="text", text="")]),
        NS(stop_reason="tool_use", model="other", content=[_block(type="fallback"), _block(type="tool_use", id="t1", name="inspect", input={})]),
    ]  # fmt: skip

    def stream(**kw):
        sent.append({**kw, "messages": list(kw["messages"])})
        return FakeStream(replies.pop(0))

    client = NS(beta=NS(messages=NS(stream=stream)))
    m = agent.AnthropicModel("claude-opus-5-5", client)
    m.start("sys", "go")
    reply = m.step([], "")
    assert reply.calls[0].name == "inspect" and reply.served_by == "other"
    assert len(sent) == 2 and sent[0]["fallbacks"] == "default" and sent[0]["output_config"] == {"effort": "high"}
    m.client = NS(
        beta=NS(messages=NS(stream=lambda **kw: FakeStream(NS(stop_reason="refusal", model="m", content=[]))))
    )
    with pytest.raises(RuntimeError, match="declined"):
        m.step([("t1", "sheets", False)])
    assert m.messages[-2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "sheets",
        "is_error": False,
    }


def test_the_openai_adapter_maps_tools_and_tool_calls():
    call = NS(id="x1", function=NS(name="decide", arguments='{"plan": "p"}'))
    message = NS(content=None, tool_calls=[call], model_dump=lambda **kw: {"role": "assistant", "tool_calls": []})
    create_args = {}

    def create(**kw):
        create_args.update(kw)
        return NS(choices=[NS(finish_reason="tool_calls", message=message)])

    m = agent.OpenAIModel("gpt", client=NS(chat=NS(completions=NS(create=create))))
    m.start("sys", "go")
    reply = m.step([], "")
    assert reply.calls[0].input == {"plan": "p"}
    assert create_args["tools"][0]["function"]["name"] == "inspect"
    m.step([("x1", "ok", False)], "more")
    assert m.messages[-3:-1] == [
        {"role": "tool", "tool_call_id": "x1", "content": "ok"},
        {"role": "user", "content": "more"},
    ]


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="expected anthropic"):
        agent.model("mistral:large")
