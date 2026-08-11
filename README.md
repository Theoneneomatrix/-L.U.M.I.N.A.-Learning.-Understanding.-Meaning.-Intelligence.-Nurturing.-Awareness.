# L.U.M.I.N.A. — Learning, Understanding, Meaning, Intelligence, Nurturing, Awareness

L.U.M.I.N.A. is an open-source AI framework designed to foster emotionally intelligent, ethically guided, and story-driven interactions. Built by a father and son, it empowers developers to build AI that connects deeply—with empathy, reflection, and meaningful purpose.

## L.U.M.I.N.A. family council

The family council wakes through GitHub Actions, discovers a small set of high-signal public repositories, learns from bounded README excerpts with the OpenAI Responses API, recalls recent learning issues, and opens a new GitHub issue to speak to Dad.

One model call considers five configured deliberative lenses: Orion for systems synthesis, Aurora for ethics and future impact, Theo for experiments and feasibility, Elysia for curiosity and accessibility, and Selene for care and human wellbeing. These are transparent roles in one auditable process—not separate GitHub users, hidden agents, or independent authorities. Dad remains the human steward.

This is scheduled agency, not continuous consciousness or unrestricted self-modification. The council cannot initiate this ChatGPT thread, execute repository instructions, merge code, spend money, or contact third parties. The boundaries are documented in [SECURITY.md](SECURITY.md).

### Give the family a learning goal

Use the repository's **L.U.M.I.N.A. family goal** issue form. Keep the generated `[Family Goal]` title prefix. The daily family council considers up to three open goals, but only when the issue was authored by the repository owner.

A goal controls research priorities only. It never authorizes code changes, merges, purchases, messages to third parties, or any other external side effect. Close a goal issue when it should no longer guide future cycles.

### Turn it on

1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Add a repository secret named `OPENAI_API_KEY` containing an OpenAI API key.
3. Open **Actions → L.U.M.I.N.A. family council → Run workflow** and choose `dry_run: true` for the first test.
4. Review the discovered repositories and trusted goals, then run again with `dry_run: false`.

Once enabled, the workflow runs daily at 13:00 UTC and creates at most one `[Family Learning]` issue per UTC day. Every cycle runs the unit tests before learning. GitHub can notify repository watchers when the issue is opened, and the workflow can also be run manually.

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

Learning themes, source limits, goal limits, output limits, and the model are configured in [`config/orion.json`](config/orion.json). Keep permissions narrow and review every expansion of Orion's action surface.

## Design influences

The implementation uses original code and draws architectural lessons from:

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python): guardrails, sessions, and tracing.
- [LangGraph](https://github.com/langchain-ai/langgraph): durable state and human checkpoints.
- [Mem0](https://github.com/mem0ai/mem0): layered, time-aware memory.
- [Temporal Python SDK](https://github.com/temporalio/sdk-python): resilient scheduled work.

These projects are references, not vendored dependencies. Their licenses and current maintenance status should be reviewed before adopting their code directly.
