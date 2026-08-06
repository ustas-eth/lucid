# Evals

`run.py` is the Codex eval runner. It compares proactive and reactive Lucid use.
The bundled `smoke.json` checks invocation and literal preservation. Real
transcript cases show whether Lucid improves writing quality.

You can also replay a difficult exchange from your own Codex history. Keep its
case file and generated result outside this repository. The runner requires an
authenticated `codex` executable.

## Replay a personal case

1. Choose a past prompt that asked for an answer rather than actions, and record
   its exact text.
2. Run `/status` in that Codex session and record its thread ID.
3. Create a case file outside this repository with the exact prompt:

```json
[
  {
    "id": "dense-status-report",
    "source": {
      "thread_id": "THREAD_ID"
    },
    "prompt": "ORIGINAL USER PROMPT"
  }
]
```

The runner finds the matching prompt and forks immediately before its turn. If
the prompt appears more than once, set `before_turn_id` to the turn containing
the chosen occurrence, or set `last_turn_id` to the final turn included in the
fork. An absolute rollout `path` can replace `thread_id` when `last_turn_id` is
provided. Exact replay requires the prompt to begin a turn because app-server
forks use turn boundaries.

Omit `source` to test a first-turn prompt with a fresh thread.

4. Run the comparison:

```sh
./evals/run.py --cases /path/to/cases.json \
  --model MODEL --effort EFFORT > /tmp/lucid-eval.json
```

5. Compare `baseline`, `reactive`, and `proactive` in the result. Check that the
   Lucid answers are easier to understand and preserve the original facts,
   decisions, instructions, commands, paths, and numbers.

Each case may also set `proactive_prompt` and `required_literals`. The runner
creates two isolated ephemeral forks. One produces a baseline and then receives
bare `$lucid`; the other receives `$lucid PROMPT` directly. It removes its
temporary app-server process, workspace, and skill registration on exit.

`invariants_passed` reports whether every required literal survived both Lucid
answers. It is `null` for cases without `required_literals`. Read the recorded
answers to judge clarity and preservation of the full meaning.
