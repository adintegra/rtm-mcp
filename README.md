# rtm-mcp

An MCP server that connects [Remember The Milk](https://www.rememberthemilk.com/) task management with your calendar availability. It lets AI assistants (Claude Desktop, OpenCode, etc.) view your tasks, check your free time, and reschedule due dates so work fits into the time you actually have.

## What it does

You have tasks in RTM with estimates and due dates. You have meetings in your calendar. This server bridges the two:

- **Lists tasks** from RTM with priority, due date, and estimate info
- **Computes free capacity** from your calendar's freebusy feed, respecting configurable work windows, meeting buffers, and minimum focus blocks
- **Plans a schedule** by fitting tasks into available slots across the next N working days
- **Applies the plan** by writing updated due dates back to RTM

The scheduling algorithm prioritises by task priority (P1 > P2 > P3), then creation date (oldest first), then estimate size (shortest first). Tasks tagged `nomove` stay pinned to their current date. Weekends are skipped. Tasks without time estimates are excluded from planning.

## Tools

| Tool | Description | Writes to RTM? |
|------|-------------|:--------------:|
| `list_tasks` | List incomplete tasks (filter: all, today, week, overdue) | No |
| `capacity` | Show free working time per day from calendar | No |
| `plan` | Preview task scheduling against calendar slots | No |
| `apply` | Write rescheduled due dates to RTM | **Yes** |

All tools return JSON with a `summary` text field for human-readable output alongside structured data.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An RTM API key, shared secret, and auth token ([RTM API docs](https://www.rememberthemilk.com/services/api/))
- A calendar freebusy ICS feed URL (e.g. from Fastmail, Google Calendar, etc.)

## Installation

```bash
git clone https://github.com/Adintegra/rtm-mcp.git
cd rtm-mcp
uv sync
```

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `RTM_API_KEY` | Yes | | RTM API key |
| `RTM_SHARED_SECRET` | Yes | | RTM shared secret |
| `RTM_AUTH_TOKEN` | Yes* | | RTM auth token |
| `RTM_TOKEN_FILE` | Yes* | | Path to file containing auth token (alternative to `RTM_AUTH_TOKEN`) |
| `RTM_CALENDAR_ICS_URL` | Yes | | URL to your freebusy `.ics` feed |
| `RTM_TZ` | No | `Europe/Zurich` | Your timezone (IANA format) |
| `RTM_WORK_WINDOWS` | No | `09:00-12:00,13:30-18:00` | Comma-separated time ranges defining working hours |
| `RTM_MEETING_BUFFER_MIN` | No | `10` | Minutes of buffer around each meeting |
| `RTM_FOCUS_BLOCK_MIN` | No | `30` | Minimum free block size (shorter gaps are ignored) |

*Provide either `RTM_AUTH_TOKEN` or `RTM_TOKEN_FILE`, not both.

## Claude Desktop setup

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "rtm-planner": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/rtm-mcp", "rtm-mcp"],
      "env": {
        "RTM_API_KEY": "your-api-key",
        "RTM_SHARED_SECRET": "your-shared-secret",
        "RTM_AUTH_TOKEN": "your-auth-token",
        "RTM_CALENDAR_ICS_URL": "https://your-freebusy-feed-url/calendar.ics",
        "RTM_TZ": "Europe/Zurich"
      }
    }
  }
}
```

Restart Claude Desktop after editing. The server will appear in the MCP tools list.

## Usage examples

Once connected, you can ask your AI assistant things like:

- "Show me my tasks for this week"
- "What does my calendar availability look like for the next 10 days?"
- "Plan my tasks for the next two weeks"
- "That plan looks good -- apply it"

The `apply` tool has a safety flag (`confirm`) that must be explicitly set to `true`. The tool description instructs the AI to always run `plan` first and confirm with you before applying.

## Running standalone

You can also run the server directly for testing:

```bash
# Start the MCP server (stdio transport)
uv run rtm-mcp
```

Or verify the tools are registered:

```bash
uv run python -c "
from rtm_mcp.server import mcp
tools = mcp._tool_manager._tools
for name in tools:
    print(f'  {name}')
"
```

## Architecture

```
src/rtm_mcp/
  server.py        MCP server entry point (FastMCP, stdio transport)
  rtm_client.py    RTM REST API client (signing, auth, task CRUD)
  calendar.py      ICS feed parsing, interval arithmetic, capacity computation
  planner.py       Scheduling algorithm (plan + apply)
  models.py        Data structures (RTMTask, DayCapacity, PlanResult, etc.)
```

The codebase is structured as a clean separation of concerns:

**`models.py`** defines pure data structures with no logic or I/O. Everything else passes these around.

**`rtm_client.py`** handles all communication with the RTM REST API. It implements the MD5-based request signing that RTM requires, fetches and parses task lists, and provides write operations (setting due dates via timelines). It has no knowledge of calendars or scheduling.

**`calendar.py`** handles the calendar side. It fetches an ICS freebusy feed, parses VEVENT blocks (handling RFC 5545 line folding, timezone conversion, and both UTC and floating datetime formats), then provides interval arithmetic (merge, subtract) to compute free working slots. Configuration for work windows, meeting buffers, and minimum focus blocks lives here.

**`planner.py`** ties the two together. It fetches tasks from RTM and busy intervals from the calendar, then runs a greedy bin-packing algorithm: for each working day, compute available capacity, subtract fixed (`nomove`) tasks, then fill remaining capacity with tasks sorted by priority and age. `plan_tasks()` returns structured results; `apply_plan()` writes the changes back to RTM.

**`server.py`** is the thin MCP layer. It instantiates a `FastMCP` server with four tools, each of which calls into the modules above and formats the results as JSON with a human-readable `summary` field. No business logic lives here -- it's purely tool definitions and output formatting.

### Design decisions

- **No external dependencies beyond `mcp`**. The RTM client uses only `urllib` and `hashlib` from the standard library. The ICS parser is hand-rolled (no `icalendar` dependency) since we only need VEVENT start/end times.
- **Stdio transport**. This is the standard for local MCP servers used by Claude Desktop and similar tools. No HTTP server, no port management.
- **Environment-based configuration**. Credentials and settings are passed via env vars, which integrates naturally with the Claude Desktop `mcpServers` config and avoids config files with secrets on disk.
- **Safety on writes**. The `apply` tool requires an explicit `confirm=true` parameter and its description tells the AI to preview first. This creates a natural human-in-the-loop workflow.

## Origin

This project started as a CLI tool used in an [OpenClaw](https://openclaw.ai) agent workspace, with an n8n wrapper for workflow automation. The MCP server extracts and refactors that logic into a clean, importable library.

## License

MIT
