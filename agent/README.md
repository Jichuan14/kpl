# KPL Draft Coach Agent

This directory is the project ledger for the agent work. Executable backend
code remains under `backend/app`; this directory records scope, decisions,
progress, and evaluation cases so the agent does not grow beyond its stated
phase.

## Documents

- `PHASE_1_QUESTIONS.md`: questions Phase 1 may and may not answer.
- `PHASE_2_QUESTIONS.md`: team-aware Phase 2 questions and remaining boundaries.
- `ROADMAP.md`: ordered tasks, status, and completion gates.
- `DECISIONS.md`: durable architecture decisions and their rationale.
- `WORKLOG.md`: append-only implementation and verification notes.
- `evals/phase_1_cases.jsonl`: representative questions and expected routing.
- `evals/phase_1_live_report.json`: latest bounded live evaluation result.

## Working rules

1. A new tool must answer a question listed in `PHASE_1_QUESTIONS.md`.
2. Only one roadmap task may be in progress at a time.
3. Heavy analysis runs after data synchronization, never inside a chat request.
4. Runtime tools use cached artifacts or bounded, parameterized SQLite queries.
5. The language model may call registered tools; it may not generate or run SQL.
6. Every factual answer must include available evidence and uncertainty.
7. Every new tool needs unit tests and at least one evaluation case.
8. Unsupported questions receive a limitation instead of an invented answer.

## Agent backend structure

```text
backend/app/
├── agent/
│   ├── service.py
│   ├── prompts.py
│   ├── tool_registry.py
│   ├── artifact_cache.py
│   ├── eval_phase1.py
│   └── tools/
│       ├── battles.py
│       ├── draft.py
│       ├── relationships.py
│       ├── teams.py
│       └── meta.py
└── api/
    └── coach.py
```

Run the deterministic catalog and regression gate without an API call:

```bash
cd backend
./.venv/bin/python -m app.agent.eval_phase1
./.venv/bin/python -m app.agent.eval_phase2
```

Run the bounded live Kimi gate only when a paid end-to-end evaluation is
intended:

```bash
./.venv/bin/python -m app.agent.eval_phase1 --live
./.venv/bin/python -m app.agent.eval_phase2 --live
```

## Local Kimi API key

The Kimi key is a backend runtime secret. It must never appear in committed
files, frontend code, logs, screenshots, or tool results.

1. Keep the real value only in `backend/.env`:

   ```text
   MOONSHOT_API_KEY=your-real-key
   ```

2. Keep `backend/.env.example` as a blank, commit-safe template.
3. Verify ignore coverage with `git check-ignore -v backend/.env`.
4. Before committing, check `git diff --cached` for accidental secrets.
5. If a real key is ever committed, revoke it immediately and create a new one;
   deleting it in a later commit is not sufficient.

The application reads the key as a masked `SecretStr` and creates the Kimi
client lazily. Structured logs record model, duration, token usage, and tool
names, but never the key or full prompts.

After adding the key, verify the real backend client with one small live call:

```bash
cd backend
./.venv/bin/python -m app.agent.smoke_test
```

The command prints the endpoint and model, confirms that a hidden key was
loaded, and reports the answer and token count. It never prints the secret.
