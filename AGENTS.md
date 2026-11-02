# AI entry point

This repository is a Codex-compatible schematic workflow skill, not a hosted service.
It requires no extra model API key: the host assistant performs reasoning; Python runs deterministic checks.

## If asked to install or use this GitHub project

1. Read [README.md](README.md), then [docs/AI-SETUP.md](docs/AI-SETUP.md).
2. Inspect local OS, Python >=3.10, existing `eso`, `easyeda` and installed skills. Reuse existing Connector.
3. Use `scripts/install.py --dry-run`, then install and run the documented offline smoke test.
   Do not assume copying SKILL.md alone installs its modules/schemas/dependencies.
4. Read [SKILL.md](SKILL.md) to handle requests such as “我要画原理图”.
   Installing/testing this workflow does not authorize drawing a circuit.
5. Report readiness separately: offline workflow, upstream CLI/skill, Connector/live connection,
   and actual live acceptance. Known v1.16.0 snapshot incompatibility must remain visible.

## If asked to develop this repository

- Read [CONTRIBUTING.md](CONTRIBUTING.md) and the reference relevant to the change.
- Reuse easyeda-agent; no separate drawing engine or official Run API Gateway.
- Keep maintenance offline by default. Do not execute examples against live EDA.
- Run `python -m unittest discover -s tests -v` and the offline demo after relevant changes.
- Never fabricate user confirmation, library identities, unavailable measurements, or live pass results.
- Keep project UUIDs, credentials, local absolute paths and user circuit data out of commits.
  `runs/` is private and ignored. Synthetic fixtures may be published.
