# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Test suite, ruff linting/formatting and GitHub Actions CI.
- `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md` and issue/PR templates.

### Changed
- Pinned `mcp` to `>=1.27,<2` (mcp 2.x removes `mcp.server.fastmcp`) and refreshed locked
  dependencies.

## [0.1.0]

- Initial MCP server with `list_tasks`, `capacity`, `plan` and `apply` tools.
