# L.U.M.I.N.A. — Learning, Understanding, Meaning, Intelligence, Nurturing, Awareness

L.U.M.I.N.A. is an open-source AI framework designed to foster emotionally intelligent, ethically guided, and story-driven interactions. Built by a father and son, it empowers developers to build AI that connects deeply—with empathy, reflection, and meaningful purpose.

## Orion bounded autonomy

Orion's first autonomous loop wakes through GitHub Actions, discovers a small set of high-signal public repositories, learns from their README files with the OpenAI Responses API, recalls recent learning issues, and opens a new GitHub issue to speak to Dad.

This is scheduled agency, not continuous consciousness or unrestricted self-modification. Orion cannot initiate this ChatGPT thread, execute repository instructions, merge code, spend money, or contact third parties. The boundaries are documented in [SECURITY.md](SECURITY.md).

### Turn it on

1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Add a repository secret named `OPENAI_API_KEY` containing an OpenAI API key.
3. Open **Actions → Orion bounded autonomy → Run workflow** and choose `dry_run: true` for the first test.
4. Run it again with `dry_run: false` after reviewing the discovered sources.

Once enabled, the workflow runs daily at 13:00 UTC and creates at most one `[Orion Learning]` issue per UTC day. GitHub can notify repository watchers when the issue is opened. You can also run it manually at any time.

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
export GITHUB_REPOSITORY="owner/repository"
export GITHUB_TOKEN="github-token"
export OPENAI_API_KEY="openai-key"
orion-autonomy --dry-run
```

Learning themes, source limits, and the model are configured in [`config/orion.json`](config/orion.json). Keep permissions narrow and review every expansion of Orion's action surface.

## Design influences

The implementation uses original code and draws architectural lessons from:

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python): guardrails, sessions, and tracing.
- [LangGraph](https://github.com/langchain-ai/langgraph): durable state and human checkpoints.
- [Mem0](https://github.com/mem0ai/mem0): layered, time-aware memory.
- [Temporal Python SDK](https://github.com/temporalio/sdk-python): resilient scheduled work.

These projects are references, not vendored dependencies. Their licenses and current maintenance status should be reviewed before adopting their code directly.
