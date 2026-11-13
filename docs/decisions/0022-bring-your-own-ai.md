# ADR 0022: bring your own AI, with operator trust for job-declared models

- **Status:** accepted for 0.6.0.
- **Requirements:** AI-001–005, INT-002, REPAIR-001, SEC-004, SEC-005, SEC-011, P-001.

## Problem

Users have different AI: a local model, an HTTP API, a subscription CLI, an SDK they call
from Python, or an MCP agent. Rastro must not be tied to one provider. At the same time a
job file is data that agents can write through MCP and Studio. A security audit showed a
hostile job could point `[model]` at its own endpoint with `api_key_env` naming any process
secret and `allow_remote = true`, and Rastro would send the secret as a bearer token. A
command transport declared in a job would be arbitrary command execution from data.

## Options

1. **One adapter per product** (Claude, Codex, Gemini...). Every new product needs code;
   rejected by the owner.
2. **Generic transports plus documented presets.** OpenAI-compatible HTTP, any local
   command (no shell, prompt on stdin, bounded stdout, allow-listed environment) and an
   in-process function cover every product. Products are documented argv/endpoint presets.
3. For job files: **trust the job's `allow_remote`** (the 0.5 behaviour), **forbid models in
   job files**, or **let the job declare and the operator consent**.

## Decision

Option 2 behind `rastro.model(spec=None, **options)` and `RASTRO_MODEL`; `ModelPort`
stays the only provider interface, and a model passed in code is trusted.

For job files, the job declares and the operator consents. A job-declared model may use a
loopback endpoint on a local model server's default port (11434, 1234, 8000, 8080) without
consent. Any other endpoint, `base_url_env` or `allow_remote = true` needs operator remote
consent (`--allow-remote`, `allow_remote=True`, `RASTRO_ALLOW_REMOTE=1`); a command needs
`--allow-command`; secret variable names outside `RASTRO_*` need `--allow-env NAME` or
`RASTRO_ALLOWED_ENV`. Checks run before any request or secret read and fail closed with
an error naming the flag. CLI flags apply process-wide, so `rastro mcp` and
`rastro studio` inherit them; SDK keywords are scoped to the call.

## Consequences

- 0.5 jobs that used a remote endpoint or a non-`RASTRO_` key name now need the operator
  flag or a renamed variable. Loopback Ollama jobs on the default port keep working.
- Loopback on the default ports stays allowed: a local process listening there could still
  receive page content. Use egress controls if that matters.
- Model output remains untrusted; consent decides where data may go, not whether a
  proposal is accepted. Every proposal passes the same deterministic gates.
