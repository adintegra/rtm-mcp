# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Test suite (75 tests, no network required) covering ICS parsing, interval arithmetic,
  capacity computation, the RTM client and the planner.
- Ruff linting and formatting configuration.
- GitHub Actions CI (lint + tests on Python 3.11-3.13) with actions pinned to commit SHAs.
- `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates and this changelog.
- Package metadata in `pyproject.toml` (readme, license, authors, keywords, classifiers, URLs)
  and a `dev` dependency group.

### Changed
- Pinned `mcp` to `>=1.27,<2` (mcp 2.x removes `mcp.server.fastmcp`) and refreshed locked
  dependencies (`mcp` 1.27.0 -> 1.30.0).
- Applied `ruff format` across the code base and removed unused imports (no behaviour change).
- README: added a Development section, fixed the clone URL, replaced the tool-listing snippet
  with the public `list_tools()` API, and reworded the Origin section.

## [0.1.0]

- Initial MCP server with `list_tasks`, `capacity`, `plan` and `apply` tools.
