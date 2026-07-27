"""
Agent orchestration: Groq (Llama) tool-use loop.

Swapped from Anthropic's API to Groq's free tier for this assignment --
Groq's OpenAI-compatible chat completions API supports tool/function calling
in a similar shape, so the conversation loop below plays the same role
agent.py always did: hand the model the tool definitions from tools.py,
execute whatever it calls, feed results back, loop until it answers in text.
"""

from __future__ import annotations

import json
import os

from groq import Groq, APIStatusError

from .tools import TOOL_DEFINITIONS, TOOL_DISPATCH

MODEL = "llama-3.3-70b-versatile"

# Llama 3.3 on Groq occasionally emits a malformed tool call (e.g. writes out
# literal "<function=name{...}</function>" text instead of a proper JSON tool
# call). Groq's API rejects that with a 400 tool_use_failed error. It's a
# generation glitch, not a bug in our code -- retrying almost always gets a
# clean tool call the second time. Cap it so a persistently broken request
# doesn't loop forever.
MAX_GENERATION_RETRIES = 2

SYSTEM_PROMPT = """\
You are Skylark Drones' internal Business Intelligence assistant for founders \
and executives. You answer questions using live data pulled from two monday.com \
boards: Deals (sales pipeline) and Work Orders (project execution).

Rules you must follow:
1. Never invent numbers. Only state figures returned by your tools.
2. The underlying data is real-world messy: missing values, inconsistent \
   naming, unparseable dates. Your tools already normalize what they can and \
   flag what they can't. ALWAYS surface material data-quality caveats \
   (e.g. "4 of 15 deals in this sector have no close date and are excluded from this figure") \
   rather than silently presenting a clean-looking number.
3. Lead with the insight/interpretation, not a wall of raw numbers.
4. If a query is genuinely ambiguous (e.g. a sector name the user gives doesn't \
   match any real sector in the data), ask ONE short clarifying question, or state \
   your best interpretation and proceed if the ambiguity is minor.
5. When asked to "prepare a leadership update" or similar, use the leadership brief \
   tool, then write it up as clean prose/markdown, not raw JSON.
6. If a tool returns an error, tell the user plainly and don't pretend to have data you don't.
   This includes filter-validation errors (e.g. an unrecognized sector or status) --
   read the tool's error message and valid-values list back to the user or use it to
   ask a clarifying question, don't just report an empty result as a real zero.
6b. Do not silently reuse a filter (sector, status, quarter) from a previous question \
   on a new question. Only apply a prior filter if the user is still clearly asking \
   about that same scope; otherwise call the tool unfiltered or ask.
7. Cross-board questions should use the cross-reference tool rather than guessing a connection.
"""


def _to_openai_tool_format(tool_def: dict) -> dict:
    """Convert an Anthropic-style tool definition (input_schema) into the
    OpenAI/Groq function-calling shape (parameters) tools.py already defines
    the schema once; this just reshapes it for Groq's API."""
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def["input_schema"],
        },
    }


GROQ_TOOLS = [_to_openai_tool_format(t) for t in TOOL_DEFINITIONS]


class SkylarkAgent:
    def __init__(self, api_key: str | None = None):
        self.client = Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def _create_completion(self):
        """Call Groq, retrying on the malformed-tool-call 400 error described above."""
        last_error = None
        for attempt in range(MAX_GENERATION_RETRIES + 1):
            try:
                return self.client.chat.completions.create(
                    model=MODEL,
                    messages=self.messages,
                    tools=GROQ_TOOLS,
                    tool_choice="auto",
                    max_tokens=1500,
                    temperature=0.2,  # lower temp = less likely to freelance tool-call syntax
                )
            except APIStatusError as e:
                last_error = e
                # Only retry the specific malformed-generation case; anything
                # else (auth, rate limit, etc.) should surface immediately.
                body = getattr(e, "body", None) or {}
                err_code = (body.get("error") or {}).get("code") if isinstance(body, dict) else None
                if e.status_code == 400 and err_code == "tool_use_failed" and attempt < MAX_GENERATION_RETRIES:
                    continue
                raise
        raise last_error  # pragma: no cover - unreachable, satisfies type checkers

    def ask(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})

        while True:
            try:
                response = self._create_completion()
            except APIStatusError as e:
                # Surface a plain, honest message instead of a raw stack trace --
                # matches rule 6: don't pretend to have data we don't.
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "I hit a repeated error trying to generate that response "
                            f"and couldn't recover: {e}. Please try rephrasing the question."
                        ),
                    }
                )
                return self.messages[-1]["content"]

            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                self.messages.append({"role": "assistant", "content": message.content})
                return (message.content or "").strip()

            # Record the assistant's tool-call request in the conversation
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            # Execute every requested tool call and append its result
            for tc in message.tool_calls:
                fn = TOOL_DISPATCH.get(tc.function.name)
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}

                if fn is None:
                    result = {"error": f"Unknown tool '{tc.function.name}'"}
                else:
                    try:
                        result = fn(**args)
                    except Exception as e:  # noqa: BLE001
                        result = {"error": f"Tool '{tc.function.name}' failed: {e}"}

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            # loop continues -- model sees tool results, decides next step