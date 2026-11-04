"""`sov2oed auto`: a model reads the schedule through the same steps as the skill, with no assistant around it.

The model gets four tools, inspect, write_rows (a PDF), decide and convert, and follows the steps of the skill:
look at the file, write the reading plan, check it (decide, at most three times), convert. It then writes the
message to the broker from the questions, in the schedule's language. Code still reads every value: the model
only ever writes a plan, a rows script and the message.

This is the one part of sov2oed that calls the network: the schedule's content goes to the model's provider.
Providers are optional extras: `pip install "sov2oed[anthropic]"` or `"sov2oed[openai]"` (any endpoint that
speaks the OpenAI chat API, through --base-url).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import pipeline
from .plan import Plan
from .read import grid as grid_mod, inspect as views

MAX_TURNS = 40
MAX_DECIDE = 3
MAX_RESULT = 60_000  # characters of a tool result sent back to the model

SYSTEM = """You convert a property statement of values (SOV) into OED files with the sov2oed tools.

1. Look at the file with inspect: the list of sheets or pages, then rows of a sheet (with formulas and comments),
   the distinct labels of a column, a PDF page with the x position of every word, OED code lists. Look for what a
   preview hides: where each table starts and ends, total rows, the unit and basis of the amounts, rows that are
   not buildings, hidden sheets, rare labels in coded columns.
2. A PDF: write a Python script defining rows(lines) and pass it to write_rows (inspect guide=rows shows one).
3. Write the reading plan as YAML, following inspect guide=guide; inspect guide=plan is a complete example. A
   value you state yourself cites its cell and quotes it exactly, or says assumed: true. Never type a value of the
   schedule into the plan.
4. decide checks the plan. Fix the plan for problems and findings of kind plan; keep findings of kind file, they
   become questions for the broker; never bend the plan to make a total match. At most three decide calls.
5. convert writes the outputs and returns the questions.

Then answer with the message to the broker only: in the language of the schedule, a greeting, every question of
questions.txt in the same order with its cells and locations, in the words of a property schedule (never an OED
field name or code), a line saying the annotated workbook is attached."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "inspect",
        "description": "Look at the schedule. With no option: its sheets or pages. Options: sheet + rows (rows with "
        "column letters, formulas, comments), sheet + labels (distinct values of a column), page (+ lines) for a "
        "PDF page with word positions, codes (an OED code list), guide (guide | plan | rows), plan_schema.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet": {"type": "string"},
                "rows": {"type": "string", "description": "rows of the sheet: 1-40,52"},
                "labels": {"type": "string", "description": "a column letter of the sheet"},
                "page": {"type": "integer"},
                "lines": {"type": "string", "description": "lines of the page: 1-30"},
                "codes": {"type": "string", "description": "OccupancyCode, ConstructionCode, RoofCover..."},
                "guide": {"type": "string", "enum": ["guide", "plan", "rows"]},
                "plan_schema": {"type": "boolean"},
            },
        },
    },
    {
        "name": "write_rows",
        "description": "A PDF: run a script defining rows(lines) on the PDF's lines and write the rows file the plan "
        "reads (rows_file: STEM.rows.csv). Returns the number of rows and the first ones.",
        "input_schema": {
            "type": "object",
            "properties": {"script": {"type": "string"}, "pages": {"type": "string", "description": "2-10"}},
            "required": ["script"],
        },
    },
    {
        "name": "decide",
        "description": "Check the reading plan (YAML text) against the file: rows read and skipped, values, findings.",
        "input_schema": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]},
    },
    {
        "name": "convert",
        "description": "Convert the file with the last plan passed to decide: OED files, report, questions, workbook.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


@dataclass
class Call:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Reply:
    text: str
    calls: list[Call]
    served_by: str | None = None  # the model that answered when it is not the one asked (a fallback)


class Model(Protocol):
    name: str

    def start(self, system: str, user: str) -> None: ...

    def step(self, results: list[tuple[str, str, bool]], note: str = "") -> Reply:
        """The model's next turn, after the results (call id, content, is_error) of its calls and a note."""
        ...


class AnthropicModel:
    """Claude through the Anthropic SDK: high effort, and server-side fallbacks when a request is declined."""

    def __init__(self, model: str, client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.name, self.client = model, client
        self.messages: list[dict[str, Any]] = []
        self.system = ""

    def start(self, system: str, user: str) -> None:
        self.system, self.messages = system, [{"role": "user", "content": user}]

    def step(self, results: list[tuple[str, str, bool]], note: str = "") -> Reply:
        content: list[dict[str, Any]] = [{"type": "tool_result", "tool_use_id": i, "content": c, "is_error": e}
                                         for i, c, e in results]  # fmt: skip
        if note:
            content.append({"type": "text", "text": note})
        if content:
            self.messages.append({"role": "user", "content": content})
        while True:
            with self.client.beta.messages.stream(
                model=self.name, max_tokens=32000, system=self.system, tools=TOOLS, messages=self.messages,
                output_config={"effort": "high"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            ) as stream:  # fmt: skip
                msg = stream.get_final_message()
            self.messages.append({"role": "assistant", "content": msg.content})
            if msg.stop_reason != "pause_turn":
                break
        if msg.stop_reason == "refusal":
            raise RuntimeError("the model declined the request")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("the model's answer was cut at its token limit")
        served = msg.model if any(getattr(b, "type", "") == "fallback" for b in msg.content) else None
        calls = [Call(b.id, b.name, dict(b.input)) for b in msg.content if b.type == "tool_use"]
        return Reply("".join(b.text for b in msg.content if b.type == "text"), calls, served)


class OpenAIModel:
    """Any model behind the OpenAI chat API (OpenAI, Azure OpenAI, a local server), through the OpenAI SDK."""

    def __init__(self, model: str, base_url: str | None = None, client: Any = None):
        if client is None:
            import openai

            client = openai.OpenAI(base_url=base_url)
        self.name, self.client = model, client
        self.messages: list[dict[str, Any]] = []
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["input_schema"]}} for t in TOOLS]  # fmt: skip

    def start(self, system: str, user: str) -> None:
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self, results: list[tuple[str, str, bool]], note: str = "") -> Reply:
        self.messages += [{"role": "tool", "tool_call_id": i, "content": c} for i, c, _ in results]
        if note:
            self.messages.append({"role": "user", "content": note})
        out = self.client.chat.completions.create(model=self.name, messages=self.messages, tools=self.tools)
        choice = out.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("the model's answer was cut at its token limit")
        msg = choice.message
        self.messages.append(msg.model_dump(exclude_none=True))
        calls = [Call(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in msg.tool_calls or []]
        return Reply(msg.content or "", calls)


def model(llm: str, base_url: str | None = None) -> Model:
    """provider:model, as given to --llm."""
    provider, _, name = llm.partition(":")
    if provider == "anthropic" and name:
        return AnthropicModel(name)
    if provider == "openai" and name:
        return OpenAIModel(name, base_url)
    raise ValueError(f"--llm {llm!r}: expected anthropic:<model> or openai:<model>")


@dataclass
class Session:
    """The tools, bound to one schedule and one output folder."""

    path: Path
    out: Path
    decides: int = 0
    plan_path: Path | None = None
    result: pipeline.Result | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    def run(self, call: Call) -> tuple[str, bool]:
        spec = next((t["input_schema"] for t in TOOLS if t["name"] == call.name), None)
        out: Any
        if spec is None:
            out, error = f"no tool {call.name}", True
        elif bad := sorted(set(call.input) - set(spec["properties"]) | set(spec.get("required", [])) - set(call.input)):
            out, error = f"{call.name}: unknown or missing arguments {', '.join(bad)}", True
        else:
            try:
                out, error = getattr(self, f"_{call.name}")(**call.input), False
            except pipeline.user_errors() as e:
                out, error = pipeline.explain(e), True
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
        self.log.append({"tool": call.name, "error": error, "chars": len(text)})
        if len(text) > MAX_RESULT:
            text = text[:MAX_RESULT] + f"\n[cut at {MAX_RESULT:,} characters: ask for fewer rows or lines]"
        return text, error

    def _inspect(self, sheet: str | None = None, rows: str | None = None, labels: str | None = None,
                 page: int | None = None, lines: str | None = None, codes: str | None = None,
                 guide: str | None = None, plan_schema: bool = False) -> Any:  # fmt: skip
        if plan_schema:
            return Plan.model_json_schema()
        if guide:
            return views.guide(guide)
        if codes:
            return views.codes(codes)
        g = grid_mod.load(self.path)
        if page:
            return views.page(g, page, lines)
        if sheet and labels:
            return views.labels(g, sheet, labels, rows or f"1-{g.sheets[sheet].max_row}")
        if sheet:
            return views.rows(g, sheet, rows or "1-40")
        return views.overview(g)

    def _write_rows(self, script: str, pages: str | None = None) -> Any:
        return pipeline.write_rows(self.path, script, self.out / f"{self.path.stem}.rows.csv", pages)

    def _decide(self, plan: str) -> Any:
        if self.decides >= MAX_DECIDE:
            return {"ok": False, "problems": [f"decide was called {MAX_DECIDE} times: call convert now"]}
        parsed = Plan.read(plan)  # a YAML or schema error does not count as a decide call
        self.decides += 1
        self.out.mkdir(parents=True, exist_ok=True)
        self.plan_path = self.out / f"{self.path.stem}.plan.yaml"
        self.plan_path.write_text(parsed.to_yaml(), encoding="utf-8")
        return pipeline.decide(self.path, self.plan_path)

    def _convert(self) -> Any:
        if self.plan_path is None:
            return {"ok": False, "problems": ["no plan yet: pass one to decide first"]}
        try:
            self.result = pipeline.convert(self.path, self.plan_path, self.out)
        except pipeline.PlanError as e:
            return {"ok": False, "problems": e.problems}
        return {**self.result.summary, "questions_txt": self.result.files["questions"].read_text(encoding="utf-8")}


def run(path: str | Path, out: str | Path, llm: Model, max_turns: int = MAX_TURNS) -> dict[str, Any]:
    """Convert one schedule with the model; the broker message goes to broker_message.txt, the run to agent.json."""
    s = Session(Path(path), Path(out))
    s.out.mkdir(parents=True, exist_ok=True)
    llm.start(SYSTEM, f"Convert this schedule to OED: {s.path.name}. Then write the message to the broker.")
    results: list[tuple[str, str, bool]] = []
    note, nudges, served = "", 0, set()
    for turn in range(1, max_turns + 1):  # noqa: B007 - the number of turns is reported
        reply = llm.step(results, note)
        if reply.served_by:
            served.add(reply.served_by)
        results, note = [(c.id, *s.run(c)) for c in reply.calls], ""
        if reply.calls:
            continue
        if s.result is not None:
            break
        if (nudges := nudges + 1) > 2:
            raise RuntimeError("the model stopped before convert")
        note = "The conversion is not done: use the tools, then convert."
    else:
        raise RuntimeError(f"no conversion after {max_turns} turns")
    assert s.result is not None
    message = s.out / "broker_message.txt"
    message.write_text(reply.text.strip() + "\n", encoding="utf-8")
    summary = {"model": llm.name, "served_by": sorted(served), "turns": turn, "decide_calls": s.decides,
               "tools": s.log, **s.result.summary}  # fmt: skip
    (s.out / "agent.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"files": {k: str(v) for k, v in s.result.files.items()} | {"broker_message": str(message)}, **summary}
