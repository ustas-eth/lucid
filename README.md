# Lucid

Lucid is a small writing skill for LLM agents. Invoke it when the current work
should use clear, familiar language instead of dense or invented terminology.

The skill changes how the agent writes, not what it does. The surrounding
conversation still determines the task, its scope, and the form of the result.

## Use

In Codex, invoke the skill directly:

```text
$lucid
```

Use it by itself when the current context already makes the next step clear, or
include it with a request:

```text
$lucid Explain why this migration is risky.
```

Codex uses `$` mentions for installed skills. The `/skills` command opens the
skill picker if you prefer to select it there.

Lucid uses ISO 24495-1 as its main plain-language reference. CEFR B1 and
ASD-STE100 provide lightweight checks for sentence complexity, direct wording,
and consistent terminology. The skill does not claim strict conformance to any
of them.

## Install

```sh
codex plugin marketplace add ustas-eth/lucid
codex plugin add lucid@lucid
```

Open a new Codex session after installation.

## License

MIT
