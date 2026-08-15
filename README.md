# Lucid

Lucid is a small skill that helps an AI agent write in clear, familiar language
instead of dense or invented terminology.

The skill controls wording and structure. The surrounding conversation
determines the task, its scope, and the form of the result.

## Use

Attach Lucid to a request to shape the next answer:

```text
$lucid Explain why this migration is risky.
```

Invoke it by itself when the conversation has become hard to follow:

```text
$lucid
```

The bare form treats Lucid as feedback that the current exchange is not clear
enough. The agent uses the conversation to clarify what matters or continue the
current task instead of replaying the whole previous response by default.

Codex uses `$` mentions for installed skills. The `/skills` command opens the
skill picker if you prefer to select it there.

Lucid follows the plain-language principles in ISO 24495-1. CEFR B1 and
ASD-STE100 guide sentence complexity, direct wording, and consistent terms.

## Support

Lucid is currently packaged and tested for Codex. Its writing rules are
independent of the agent that runs them. Contributions that package and test
them for Claude Code, Hermes, or OpenCode are welcome.

## Install for Codex

```sh
codex plugin marketplace add ustas-eth/lucid
codex plugin add lucid@lucid
```

Open a new Codex session after installation.

## Evaluate

Run `./evals/run.py --model MODEL --effort EFFORT` for the bundled smoke cases.
See [evals](evals/README.md) to replay a difficult exchange from your own Codex
history and compare the baseline, reactive, and proactive answers.

## License

MIT
