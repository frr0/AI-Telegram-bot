# Antigravity Telegram and Terminal Bridge

An interactive bridge connecting the local Antigravity CLI agent to both a local terminal interface and a remote Telegram bot interface. This enables bidirectional interaction, remote task dispatching, live terminal monitoring, and inline decision approvals.

---

## Features

- **Remote Prompt Execution:** Send instructions and tasks to Antigravity CLI remotely via Telegram.
- **Synchronized Terminal Feed:** Real-time visibility into agent outputs, thoughts, command executions, and status updates directly in the local console.
- **Bidirectional Control:** Submit prompts interchangeably from either the local terminal or Telegram.
- **Interactive Decision Prompts:** Automatically parses multiple-choice questions and structured choices from the agent and renders them as clickable Telegram inline buttons and numbered terminal selections.
- **Persistent Mobile Navigation:** Provides a persistent Telegram reply keyboard for quick access to actions such as stopping execution, starting a new session, switching models, and checking status.
- **Model Management:** Switch between configured models (e.g., Gemini, Claude) directly from Telegram or the command line.
- **Session History:** Automatically persists conversation turns and session logs to disk.
- **Single-Instance Enforcement:** File-descriptor locking prevents duplicate background processes from conflicting on the same workspace.

---

## Architecture Overview

```
+-----------------------------------------------------------+
|                      User Devices                         |
|                                                           |
|   +-----------------------+     +---------------------+   |
|   |  Telegram Client      |     |  Local Terminal     |   |
|   |  (Mobile / Desktop)   |     |  (ANSI Console)     |   |
|   +-----------+-----------+     +----------+----------+   |
+---------------|----------------------------|--------------+
                | Long Polling / Web API     | STDIN / STDOUT
                v                            v
+-----------------------------------------------------------+
|               Antigravity Bridge (main.py)                |
|                                                           |
|   +-------------------+             +-----------------+   |
|   |  TelegramClient   | <---------> |   TerminalUI    |   |
|   +---------+---------+             +--------+--------+   |
|             |                                |            |
|             +----------------+---------------+            |
|                              |                            |
|                              v                            |
|                   +---------------------+                 |
|                   |   HistoryManager    |                 |
|                   +---------------------+                 |
|                              |                            |
|                              v                            |
|                   +---------------------+                 |
|                   |      AgyEngine      |                 |
|                   +----------+----------+                 |
+------------------------------|----------------------------+
                               | Subprocess Pipe
                               v
+-----------------------------------------------------------+
|              Antigravity CLI Binary (agy)                 |
|                                                           |
|   - Workspace Execution                                   |
|   - Tool Calling & File Management                        |
|   - LLM Reasoning & Task Resolution                       |
+-----------------------------------------------------------+
```

---

## Prerequisites

- Python 3.9 or higher
- `aiohttp` library
- Antigravity CLI installed locally (`agy`)
- A Telegram bot token obtained from `@BotFather`

---

## Installation

1. Clone the repository:
   ```bash
   git clone git@github.com:frr0/AI-Telegram-bot.git
   cd AI-Telegram-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(Or install directly: `pip install aiohttp`)*

3. Configure the environment:
   Copy the sample configuration file and populate your settings:
   ```bash
   cp config.example.json config.json
   ```

---

## Configuration

Edit `config.json` with your parameters:

```json
{
  "bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
  "allowed_user_ids": [],
  "workspace_path": "/path/to/your/workspace",
  "agy_path": "/path/to/agy",
  "default_model": "Gemini 3.8 Flash (Medium)",
  "dangerously_skip_permissions": true
}
```

### Configuration Parameters

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `bot_token` | String | Telegram bot API token from `@BotFather`. |
| `allowed_user_ids` | Array of Integers | Authorized Telegram user IDs. If empty, the first user to send `/start` is registered automatically. |
| `workspace_path` | String | Target directory where the Antigravity agent executes file operations and commands. |
| `agy_path` | String | Absolute path or system resolution path to the `agy` executable. |
| `default_model` | String | Initial model identifier used for agent sessions. |
| `dangerously_skip_permissions` | Boolean | Whether to execute CLI tools without interactive sandbox confirmation prompts. |

---

## Running the Application

Start the bridge using the provided launch script:

```bash
bash start.sh
```

Alternatively, invoke Python directly:

```bash
python3 main.py
```

### Initial Pairing
1. Start the service on the host machine.
2. Open Telegram and navigate to your bot.
3. Send the `/start` command.
4. If `allowed_user_ids` is empty, your Telegram user ID is automatically paired and saved to `config.json`. Subsequent requests from unauthorized accounts are rejected.

---

## Telegram Commands

| Command | Action |
| :--- | :--- |
| `/start` | Displays current status and registers user authorization. |
| `/menu` | Displays the interactive inline command menu. |
| `/status` | Outputs the active model, current session ID, and workspace directory. |
| `/new` | Resets the conversation context and starts a fresh turn session. |
| `/stop` | Terminates the currently executing agent process. |
| `/history` | Displays past prompt-response turns from the active session. |
| `/models` | Displays inline options to switch the underlying language model. |
| `/model <name>` | Sets the active model directly by name. |
| `/help` | Displays command reference documentation. |

---

## Decision Prompts and Inline Selection

When Antigravity requests input or user clarification (e.g., architectural choices or confirmation prompts), the bridge parses numbered options and provides:

1. **Interactive Buttons on Telegram:** Tapping an option immediately responds to the agent and resumes processing.
2. **Terminal Input:** Entering the corresponding integer choice in the host terminal selects the option locally.

Example agent prompt:
```text
Select the database backend:
1. PostgreSQL
2. SQLite
3. Redis
```

---

## File Structure

```text
.
|-- AGENTS.md               Agent interaction instructions and formatting rules
|-- agy_engine.py           Subprocess manager for Antigravity CLI invocation
|-- config.example.json     Template configuration file
|-- config.py               Configuration loading and persistence handler
|-- history_manager.py      Session history storage and retrieval
|-- main.py                 Application entry point and bidirectional event loop
|-- question_parser.py      Parser for detecting numbered choices and questions
|-- start.sh                Startup script with process termination checks
|-- telegram_client.py      Async Telegram Bot API client wrapper
`-- terminal_ui.py          ANSI terminal formatting and display logger
```

---

## License

This project is distributed under the MIT License.
