# Contributing to rtm-mcp

Thanks for your interest in contributing! This is a small project, so the process is light.

## Development setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/adintegra/rtm-mcp.git
cd rtm-mcp
uv sync          # creates .venv and installs runtime + dev dependencies
```

## Running checks

CI runs the same commands on every pull request:

```bash
uv run pytest                 # tests (no network or RTM account needed)
uv run ruff check .           # lint
uv run ruff format --check .  # formatting (use `uv run ruff format .` to fix)
```

Tests must not hit the network. The RTM API and calendar fetches are patched in the existing
tests; follow the patterns in `tests/` when adding new ones.

## Making changes

1. Open an issue first for anything non-trivial so we can agree on the approach.
2. Create a branch from `main`.
3. Add or update tests for behaviour changes.
4. Update `README.md` if you change tools, configuration or behaviour, and add a line to the
   `Unreleased` section of `CHANGELOG.md`.
5. Open a pull request describing what changed and why.

## Project conventions

- Keep the layering described in the README: `models.py` is pure data, `rtm_client.py` talks to
  RTM, `calendar.py` handles the ICS feed, `planner.py` combines them, and `server.py` is only the
  thin MCP layer.
- Prefer the standard library. New runtime dependencies need a good reason.
- Tools that write to RTM (`apply`) must keep their explicit confirmation requirement.
- Never commit credentials, tokens or calendar URLs, including in tests and fixtures.

## `mcp` version pin

The project depends on `mcp>=1.27,<2`. mcp 2.x renamed `FastMCP` to `MCPServer` and changed other
APIs, so a migration is needed before the upper bound can be lifted. Please don't bump the cap
without that migration.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Please don't open public issues for vulnerabilities.
