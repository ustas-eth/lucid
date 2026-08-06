#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Callable


TRACKED_NOTIFICATIONS = {"item/completed", "turn/completed"}
STREAM_LIMIT = 8 * 1024 * 1024


class AppServer:
    def __init__(self, codex_bin: str, timeout: float):
        self.codex_bin = codex_bin
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.stderr_tail: list[str] = []
        self.notifications: list[dict[str, Any]] = []
        self.next_id = 1

    async def __aenter__(self) -> AppServer:
        self.proc = await asyncio.create_subprocess_exec(
            self.codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "lucid_eval",
                        "title": "Lucid eval",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self.send({"method": "initialized"})
        except Exception:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self.proc is None:
            return
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self.stderr_task is not None:
            await self.stderr_task
        self.proc = None

    async def send(self, message: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("app-server input is unavailable")
        self.proc.stdin.write(
            (json.dumps(message, separators=(",", ":")) + "\n").encode()
        )
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.next_id
        self.next_id += 1
        await self.send({"method": method, "id": request_id, "params": params})
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            message = await self._read(deadline, method)
            if "method" in message:
                if message.get("method") in TRACKED_NOTIFICATIONS:
                    self.notifications.append(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], separators=(",", ":")))
            if "result" not in message:
                raise RuntimeError(f"app-server method {method} returned no result")
            return message["result"]

    async def wait_for_notification(
        self, method: str, predicate: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            for index, message in enumerate(self.notifications):
                params = message.get("params")
                if (
                    message.get("method") == method
                    and isinstance(params, dict)
                    and predicate(params)
                ):
                    self.notifications.pop(index)
                    return params

            message = await self._read(deadline, method)
            params = message.get("params")
            if (
                message.get("method") == method
                and isinstance(params, dict)
                and predicate(params)
            ):
                return params
            if message.get("method") in TRACKED_NOTIFICATIONS:
                self.notifications.append(message)

    def take_notifications(
        self, method: str, predicate: Callable[[dict[str, Any]], bool]
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for message in self.notifications:
            params = message.get("params")
            if (
                message.get("method") == method
                and isinstance(params, dict)
                and predicate(params)
            ):
                matches.append(params)
            else:
                remaining.append(message)
        self.notifications = remaining
        return matches

    async def _read(self, deadline: float, label: str) -> dict[str, Any]:
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("app-server output is unavailable")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(self._error(f"timed out waiting for {label}"))
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), remaining)
        except TimeoutError as exc:
            raise TimeoutError(self._error(f"timed out waiting for {label}")) from exc
        if not line:
            raise RuntimeError(self._error("app-server exited"))
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("app-server returned invalid JSON") from exc
        if not isinstance(message, dict):
            raise RuntimeError("app-server returned a non-object message")
        return message

    async def _drain_stderr(self) -> None:
        if self.proc is None or self.proc.stderr is None:
            return
        while line := await self.proc.stderr.readline():
            self.stderr_tail.append(line.decode(errors="replace").rstrip())
            self.stderr_tail = self.stderr_tail[-20:]

    def _error(self, message: str) -> str:
        if not self.stderr_tail:
            return message
        return message + "\n" + "\n".join(self.stderr_tail)


def as_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"app-server returned invalid {label}")
    return value


async def start_thread(
    app: AppServer,
    workspace: Path,
    model: str | None,
    source: Any,
    prompt: str | None = None,
) -> str:
    params: dict[str, Any] = {
        "cwd": str(workspace),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "ephemeral": True,
    }
    if model:
        params["model"] = model
    if source is None:
        params["environments"] = []
        method = "thread/start"
    else:
        if not isinstance(source, dict):
            raise ValueError("case source must be an object")
        thread_id = source.get("thread_id")
        path = source.get("path")
        before_turn_id = source.get("before_turn_id")
        last_turn_id = source.get("last_turn_id")
        fields = (thread_id, path, before_turn_id, last_turn_id)
        if any(value is not None and not isinstance(value, str) for value in fields):
            raise ValueError("case source fields must be strings")
        if bool(thread_id) == bool(path):
            raise ValueError("case source needs exactly one of thread_id or path")
        if before_turn_id and last_turn_id:
            raise ValueError("case source cannot set both turn boundaries")
        if path:
            source_path = Path(path).expanduser()
            if not source_path.is_absolute():
                raise ValueError("case source path must be absolute")
            path = str(source_path)

        if before_turn_id or not last_turn_id:
            if not thread_id:
                raise ValueError("path sources require last_turn_id")
            turns = await read_source_turns(app, thread_id)
            turn_ids = [turn.get("id") for turn in turns]
            if before_turn_id:
                try:
                    boundary_index = turn_ids.index(before_turn_id)
                except ValueError as exc:
                    raise ValueError(
                        f"source turn {before_turn_id!r} was not found"
                    ) from exc
            else:
                if prompt is None:
                    raise ValueError("source needs a prompt or turn boundary")
                matches = [
                    index
                    for index, turn in enumerate(turns)
                    if turn_contains_prompt(turn, prompt)
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"source has {len(matches)} turns matching the prompt; "
                        "set before_turn_id or last_turn_id"
                    )
                boundary_index = matches[0]
            if boundary_index == 0:
                raise ValueError("cannot fork before the source thread's first turn")
            last_turn_id = turn_ids[boundary_index - 1]

        params["threadId"] = thread_id or ""
        params["excludeTurns"] = True
        if path:
            params["path"] = path
        if last_turn_id:
            params["lastTurnId"] = last_turn_id
        method = "thread/fork"

    result = as_object(await app.request(method, params), "thread result")
    thread = as_object(result.get("thread"), "thread")
    thread_id = thread.get("id")
    if not isinstance(thread_id, str):
        raise RuntimeError("thread/start returned no thread id")
    return thread_id


def turn_contains_prompt(turn: dict[str, Any], prompt: str) -> bool:
    for item in turn.get("items", []):
        if not isinstance(item, dict) or item.get("type") != "userMessage":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
        if text == prompt:
            return True
    return False


async def read_source_turns(
    app: AppServer,
    thread_id: str,
) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "limit": 100,
            "sortDirection": "asc",
            "itemsView": "summary",
        }
        if cursor is not None:
            params["cursor"] = cursor
        result = as_object(
            await app.request("thread/turns/list", params),
            "thread/turns/list result",
        )
        page = result.get("data")
        if not isinstance(page, list) or not all(
            isinstance(turn, dict) and isinstance(turn.get("id"), str)
            for turn in page
        ):
            raise RuntimeError("thread/turns/list returned invalid turns")
        turns.extend(page)
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            return turns
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise RuntimeError("thread/turns/list returned an invalid cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


async def run_turn(
    app: AppServer,
    thread_id: str,
    input_items: list[dict[str, str]],
    model: str | None,
    effort: str | None,
) -> str:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": input_items,
        "approvalPolicy": "never",
        "environments": [],
    }
    if model:
        params["model"] = model
    if effort:
        params["effort"] = effort
    result = as_object(await app.request("turn/start", params), "turn result")
    turn = as_object(result.get("turn"), "turn")
    turn_id = turn.get("id")
    if not isinstance(turn_id, str):
        raise RuntimeError("turn/start returned no turn id")

    completed = await app.wait_for_notification(
        "turn/completed",
        lambda value: value.get("threadId") == thread_id
        and isinstance(value.get("turn"), dict)
        and value["turn"].get("id") == turn_id,
    )
    turn = as_object(completed.get("turn"), "completed turn")
    if turn.get("status") != "completed":
        raise RuntimeError(f"turn ended with status {turn.get('status')}")
    item_events = app.take_notifications(
        "item/completed",
        lambda value: value.get("threadId") == thread_id
        and value.get("turnId") == turn_id,
    )
    items = [
        event["item"] for event in item_events if isinstance(event.get("item"), dict)
    ]
    if items:
        turn["items"] = items
    return answer_from(turn)


def answer_from(turn: dict[str, Any]) -> str:
    messages = [
        item
        for item in turn.get("items", [])
        if isinstance(item, dict)
        and item.get("type") == "agentMessage"
        and isinstance(item.get("text"), str)
    ]
    for item in reversed(messages):
        if item.get("phase") == "final_answer":
            return item["text"]
    if messages:
        return messages[-1]["text"]
    raise RuntimeError("completed turn has no agent message")


def skill_input(name: str, path: Path, text: str) -> list[dict[str, str]]:
    return [
        {"type": "text", "text": text},
        {"type": "skill", "name": name, "path": str(path)},
    ]


async def evaluate_case(
    app: AppServer,
    workspace: Path,
    skill_path: Path,
    skill_name: str,
    case: dict[str, Any],
    model: str | None,
    effort: str | None,
) -> dict[str, Any]:
    case_id = case.get("id")
    prompt = case.get("prompt")
    proactive_prompt = case.get("proactive_prompt", prompt)
    required = case.get("required_literals", [])
    source = case.get("source")
    if not all(isinstance(value, str) for value in (case_id, prompt, proactive_prompt)):
        raise ValueError("each case needs string id and prompt fields")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError(f"case {case_id}: required_literals must be a string list")

    reactive_thread = await start_thread(app, workspace, model, source, prompt)
    baseline = await run_turn(
        app, reactive_thread, [{"type": "text", "text": prompt}], model, effort
    )
    reactive = await run_turn(
        app,
        reactive_thread,
        skill_input(skill_name, skill_path, f"${skill_name}"),
        model,
        effort,
    )
    proactive_thread = await start_thread(app, workspace, model, source, prompt)
    proactive = await run_turn(
        app,
        proactive_thread,
        skill_input(skill_name, skill_path, f"${skill_name} {proactive_prompt}"),
        model,
        effort,
    )

    missing = {
        "reactive": [literal for literal in required if literal not in reactive],
        "proactive": [literal for literal in required if literal not in proactive],
    }
    invariants_passed = (
        not missing["reactive"] and not missing["proactive"] if required else None
    )
    return {
        "id": case_id,
        "baseline": baseline,
        "reactive": reactive,
        "proactive": proactive,
        "word_counts": {
            "baseline": len(baseline.split()),
            "reactive": len(reactive.split()),
            "proactive": len(proactive.split()),
        },
        "missing_literals": missing,
        "invariants_passed": invariants_passed,
    }


def copy_skill(source: Path, destination: Path, name: str) -> None:
    lines = source.read_text().splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("skill file has no YAML frontmatter")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"name: {name}{newline}"
            destination.write_text("".join(lines))
            return
    raise ValueError("skill frontmatter has no name")


async def register_skill(
    app: AppServer, workspace: Path, root: Path, name: str
) -> None:
    await app.request("skills/extraRoots/set", {"extraRoots": [str(root)]})
    result = as_object(
        await app.request(
            "skills/list", {"cwds": [str(workspace)], "forceReload": True}
        ),
        "skills/list result",
    )
    rows = result.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("skills/list returned no data")
    available = [
        skill.get("name")
        for row in rows
        if isinstance(row, dict)
        for skill in row.get("skills", [])
        if isinstance(skill, dict)
    ]
    if name not in available:
        raise RuntimeError(f"temporary skill {name!r} was not discovered")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run isolated Lucid writing checks.")
    parser.add_argument("--cases", type=Path, default=root / "evals" / "smoke.json")
    parser.add_argument(
        "--skill",
        type=Path,
        default=root / "plugins" / "lucid" / "skills" / "lucid" / "SKILL.md",
    )
    parser.add_argument("--skill-name", default="lucid-eval")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


async def run(args: argparse.Namespace, cases: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="lucid-eval-") as temporary:
        workspace = Path(temporary)
        skill_root = workspace / "skills"
        local_skill = skill_root / args.skill_name / "SKILL.md"
        local_skill.parent.mkdir(parents=True)
        copy_skill(args.skill, local_skill, args.skill_name)

        async with AppServer(args.codex_bin, args.timeout) as app:
            await register_skill(app, workspace, skill_root, args.skill_name)
            results = [
                await evaluate_case(
                    app,
                    workspace,
                    local_skill,
                    args.skill_name,
                    case,
                    args.model,
                    args.effort,
                )
                for case in cases
            ]
    checked = [
        result["invariants_passed"]
        for result in results
        if result["invariants_passed"] is not None
    ]
    return {
        "model": args.model or "configured default",
        "effort": args.effort or "configured default",
        "invariants_passed": all(checked) if checked else None,
        "cases": results,
    }


def main() -> int:
    args = parse_args()
    cases = json.loads(args.cases.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases file must contain a non-empty JSON list")
    if not args.skill.is_file():
        raise FileNotFoundError(args.skill)
    output = asyncio.run(run(args, cases))
    print(json.dumps(output, indent=2))
    return 1 if output["invariants_passed"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
