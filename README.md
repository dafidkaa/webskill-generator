# WebSkill Generator 🤖

Give any AI agent a URL + credentials — it explores the web app, maps all pages, discovers actions, intercepts API calls, and writes a **permanent executable skill** that any agent can use from then on.

Built for [OpenClaw](https://openclaw.io) agents. Works with any web app.

## What it generates

For every web app, it creates 3 files in `webskills/<app-name>/`:

| File | Purpose |
|------|----------|
| `SKILL.md` | Human + agent readable map: pages, commands, API endpoints |
| `actions.py` | Executable Playwright code for every discovered action |
| `config.json` | URL, auth method, session config, metadata |

Once generated, any agent just loads the skill and executes commands.

## Install

```bash
pip install playwright
playwright install chromium
```

## Usage

```bash
# Public app (no auth)
python3 generator.py --url https://example.com --name myapp --auth-method none

# Form login
python3 generator.py --url https://app.example.com --name myapp \
  --auth-method form --username user@email.com --password secret

# API key
python3 generator.py --url https://api.example.com --name myapp \
  --auth-method apikey --api-key YOUR_KEY

# Cookie-based session
python3 generator.py --url https://app.example.com --name myapp \
  --auth-method cookie --cookies '[{"name":"session","value":"xyz","domain":"app.example.com"}]'

# Interactive setup
python3 generator.py --interactive

# Re-explore (refresh skill after app changes)
python3 generator.py --update webskills/myapp/config.json
```

## Using a generated skill

```bash
# List all available commands
python3 webskills/myapp/actions.py --list

# Execute a command
python3 webskills/myapp/actions.py --action "Login"
python3 webskills/myapp/actions.py --action "Fill submit form" --params '{"email": "me@x.com"}'

# Interactive session
python3 webskills/myapp/actions.py --interactive
```

## Auth methods

| Method | Flag | Use when |
|--------|------|----------|
| None | `--auth-method none` | Public apps |
| Form login | `--auth-method form` | Username/password forms |
| API key | `--auth-method apikey` | REST APIs with key header |
| Cookie | `--auth-method cookie` | Existing browser sessions |

## How it works

1. **Authenticate** — logs in using your chosen auth method
2. **Crawl** — visits up to 40 pages, follows internal links
3. **Map** — extracts buttons, forms, nav items from each page
4. **Intercept** — captures JSON API calls (endpoints, methods, paths)
5. **Generate** — writes `SKILL.md` + `actions.py` + `config.json`

## Requirements

- Python 3.8+
- `playwright` (`pip install playwright && playwright install chromium`)

## Output quality

| Level | What it means |
|-------|---------------|
| ⭐ Basic | Login works, main pages mapped, 3–5 actions |
| ⭐⭐ Good | Full nav mapped, 10+ actions, forms work |
| ⭐⭐⭐ Full | API endpoints captured, all CRUD ops, JSON output |

Most apps hit ⭐⭐ on first run. Use `--update` to push toward ⭐⭐⭐.

---

Built by [Skybot](https://github.com/dafidkaa) — part of the OpenClaw agent system.
