#!/usr/bin/env python3
"""
WebSkill Generator — Skybot
Explores any web app, maps its structure, captures API calls,
and writes a permanent executable skill (SKILL.md + actions.py + config.json).

Usage:
  python3 generator.py --url https://app.example.com --name myapp --auth-method form --username u --password p
  python3 generator.py --interactive
  python3 generator.py --update /path/to/webskills/myapp/config.json
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
WEBSKILLS_DIR = Path("webskills")
SCREENSHOTS_DIR = Path("webskills/_screenshots")
MAX_PAGES = 40
MAX_ACTIONS_PER_PAGE = 20
NAV_TIMEOUT = 20_000
IDLE_TIMEOUT = 8_000


def log(msg: str, level="INFO"):
    prefix = {"INFO": "ℹ️ ", "OK": "✅", "WARN": "⚠️ ", "ERR": "❌", "STEP": "🔹"}
    print(f"{prefix.get(level,'  ')} {msg}", flush=True)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", name.lower().strip()).strip("-")


def safe_screenshot(page: Page, path: Path, name: str):
    try:
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / f"{slugify(name)}.png"), full_page=False)
    except Exception:
        pass


def wait_for_stable(page: Page, ms=IDLE_TIMEOUT):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
    time.sleep(0.5)


# ─────────────────────────────────────────────
# Auth Handlers
# ─────────────────────────────────────────────

def auth_none(page, config):
    page.goto(config["url"], timeout=NAV_TIMEOUT)
    wait_for_stable(page)
    log(f"Loaded {config['url']} (no auth)", "OK")


def auth_form(page: Page, config: dict):
    page.goto(config["url"], timeout=NAV_TIMEOUT)
    wait_for_stable(page)
    username = config.get("username", "")
    password = config.get("password", "")
    for sel in ['input[type="email"]', 'input[name="email"]', 'input[name="username"]',
                'input[name="login"]', 'input[id*="email"]', 'input[id*="user"]',
                'input[placeholder*="email" i]', 'input[placeholder*="username" i]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.fill(username)
                log(f"Filled username into {sel}", "OK")
                break
        except Exception:
            continue
    for sel in ['input[type="password"]', 'input[name="password"]', 'input[id*="pass"]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.fill(password)
                log(f"Filled password into {sel}", "OK")
                break
        except Exception:
            continue
    for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")',
                'button:has-text("Sign in")', 'button:has-text("Log in")',
                'button:has-text("Continue")', 'button:has-text("Next")']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                log(f"Clicked submit: {sel}", "OK")
                wait_for_stable(page, 12000)
                break
        except Exception:
            continue
    log(f"Auth form done. Current URL: {page.url}", "OK")


def auth_apikey(page: Page, config: dict):
    api_key = config.get("api_key", "")
    header_name = config.get("api_key_header", "Authorization")
    def add_header(route, request):
        headers = {**request.headers, header_name: api_key}
        route.continue_(headers=headers)
    page.route("**/*", add_header)
    page.goto(config["url"], timeout=NAV_TIMEOUT)
    wait_for_stable(page)
    log("API key injected via route intercept", "OK")


def auth_cookie(page: Page, config: dict):
    cookies = config.get("cookies", [])
    context = page.context
    context.add_cookies(cookies)
    page.goto(config["url"], timeout=NAV_TIMEOUT)
    wait_for_stable(page)
    log(f"Injected {len(cookies)} cookies", "OK")


AUTH_HANDLERS = {
    "none":    auth_none,
    "form":    auth_form,
    "apikey":  auth_apikey,
    "cookie":  auth_cookie,
}


class APITracker:
    def __init__(self):
        self.calls = []
        self._seen = set()

    def attach(self, page: Page):
        page.on("response", self._on_response)

    def _on_response(self, response):
        try:
            url = response.url
            method = response.request.method
            status = response.status
            ct = response.headers.get("content-type", "")
            if "json" in ct or "/api/" in url or "/v1/" in url or "/v2/" in url or "/graphql" in url:
                key = f"{method}:{url}"
                if key not in self._seen:
                    self._seen.add(key)
                    parsed = urlparse(url)
                    self.calls.append({"method": method, "url": url, "path": parsed.path,
                                       "status": status, "content_type": ct})
        except Exception:
            pass

    def get_summary(self):
        return self.calls[:100]


def extract_nav_links(page: Page, base_url: str) -> list:
    links = []
    try:
        raw = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            return links.map(a => ({href: a.href, text: a.innerText.trim().substring(0,80)}));
        }""")
        base_host = urlparse(base_url).netloc
        for item in raw:
            href = item.get("href", "")
            if not href:
                continue
            parsed = urlparse(href)
            if parsed.netloc and parsed.netloc != base_host:
                continue
            if parsed.scheme in ("mailto", "tel", "javascript"):
                continue
            if href.startswith("#"):
                continue
            full = href if href.startswith("http") else urljoin(base_url, href)
            links.append({"url": full, "text": item.get("text", "")})
    except Exception:
        pass
    return links


def extract_page_actions(page: Page) -> list:
    try:
        return page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('button, [role="button"], [type="submit"]').forEach(el => {
                const text = el.innerText?.trim() || el.getAttribute('aria-label') || el.getAttribute('title') || '';
                if (text && text.length < 100)
                    results.push({type: 'button', label: text, id: el.id || '', class: el.className?.substring(0,60) || ''});
            });
            document.querySelectorAll('form').forEach(form => {
                const fields = Array.from(form.querySelectorAll('input,select,textarea')).map(f => ({
                    name: f.name || f.id || f.placeholder || f.getAttribute('aria-label') || '',
                    type: f.type || f.tagName.toLowerCase(),
                })).filter(f => f.name);
                const submit = form.querySelector('[type="submit"]')?.innerText || 'submit';
                results.push({type: 'form', fields: fields.slice(0,15), submit: submit, action: form.action || ''});
            });
            document.querySelectorAll('nav a, [role="navigation"] a, .sidebar a, .menu a').forEach(a => {
                const text = a.innerText?.trim() || '';
                if (text && text.length < 60)
                    results.push({type: 'nav', label: text, href: a.href || ''});
            });
            return results.slice(0, 40);
        }""")
    except Exception:
        return []


def get_page_title_and_purpose(page: Page) -> dict:
    try:
        return page.evaluate("""() => {
            const title = document.title || '';
            const h1 = document.querySelector('h1')?.innerText?.trim() || '';
            const h2 = document.querySelector('h2')?.innerText?.trim() || '';
            const meta = document.querySelector('meta[name="description"]')?.content || '';
            return {title, h1, h2, meta};
        }""")
    except Exception:
        return {"title": page.title() or "", "h1": "", "h2": "", "meta": ""}


def explore_app(page: Page, config: dict, api_tracker: APITracker, screenshot_dir: Path) -> dict:
    base_url = config["url"]
    base_host = urlparse(base_url).netloc
    visited = {}
    queue = [base_url]
    seen_urls = {base_url}

    log(f"Starting exploration from {base_url}", "STEP")

    while queue and len(visited) < MAX_PAGES:
        url = queue.pop(0)
        log(f"Visiting [{len(visited)+1}/{MAX_PAGES}]: {url}")
        try:
            if page.url != url:
                page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            wait_for_stable(page)
        except PWTimeout:
            log(f"Timeout on {url}", "WARN"); continue
        except Exception as e:
            log(f"Error visiting {url}: {e}", "WARN"); continue

        current_url = page.url
        if current_url in visited:
            continue

        title_data = get_page_title_and_purpose(page)
        actions = extract_page_actions(page)
        nav_links = extract_nav_links(page, base_url)
        page_name = slugify(title_data.get("h1") or title_data.get("title") or url.split("/")[-1] or "page")
        safe_screenshot(page, screenshot_dir, page_name)

        visited[current_url] = {
            "url": current_url,
            "title": title_data.get("title", ""),
            "h1": title_data.get("h1", ""),
            "h2": title_data.get("h2", ""),
            "meta": title_data.get("meta", ""),
            "actions": actions[:MAX_ACTIONS_PER_PAGE],
            "nav_count": len(nav_links),
        }

        for link in nav_links:
            link_url = link["url"]
            link_host = urlparse(link_url).netloc
            if link_host != base_host and link_host:
                continue
            skip_patterns = [".jpg", ".png", ".pdf", ".zip", ".svg", ".ico",
                             "logout", "signout", "sign-out", "#", "javascript:"]
            if any(p in link_url.lower() for p in skip_patterns):
                continue
            if link_url not in seen_urls:
                seen_urls.add(link_url)
                queue.append(link_url)

    log(f"Exploration complete. Pages visited: {len(visited)}", "OK")
    return visited


def derive_commands(pages: dict, api_calls: list) -> list:
    commands = []
    seen_labels = set()
    for url, page in pages.items():
        for action in page.get("actions", []):
            atype = action.get("type")
            label = action.get("label", "").strip()
            if not label or label.lower() in seen_labels:
                continue
            seen_labels.add(label.lower())
            if atype == "button":
                commands.append({"command": label, "description": f"Click the '{label}' button",
                                 "page": url, "action_type": "click",
                                 "selector": f'button:has-text("{label}")'  })
            elif atype == "form":
                fields = [f["name"] for f in action.get("fields", [])]
                submit = action.get("submit", "submit")
                commands.append({"command": f"Fill {submit} form",
                                 "description": f"Fill form with fields: {', '.join(fields[:6])}",
                                 "page": url, "action_type": "form_fill",
                                 "fields": fields, "submit_label": submit})
    for call in api_calls[:20]:
        commands.append({"command": f"{call['method']} {call['path']}",
                         "description": f"API call: {call['method']} {call['url']}",
                         "action_type": "api", "method": call["method"], "url": call["url"]})
    return commands[:50]


def write_skill_md(skill_dir: Path, config: dict, pages: dict, commands: list, api_calls: list):
    app_name = config["name"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    page_rows = []
    for u, p in list(pages.items())[:30]:
        title = p.get("h1") or p.get("title") or u.split("/")[-1] or u
        page_rows.append(f"| `{u}` | {title} | {len(p.get('actions', []))} actions |")
    cmd_rows = [f"| `{c['command']}` | {c['description']} |" for c in commands[:30]]
    api_rows = [f"| {a['method']} | `{a['path']}` | {a.get('status','')} |" for a in api_calls[:15]]
    content = f"""# {app_name} — WebSkill\n\n**Generated:** {now}  \n**URL:** {config['url']}  \n**Auth method:** {config.get('auth_method', 'unknown')}  \n**Pages mapped:** {len(pages)}  \n**Commands available:** {len(commands)}\n\n---\n\n## Page Inventory\n\n| URL | Purpose | Actions |\n|-----|---------|---------|\n" + "\n".join(page_rows) + "\n\n## Commands\n\n| Command | Description |\n|---------|-------------|\n" + "\n".join(cmd_rows) + "\n\n## API Endpoints\n\n| Method | Path | Status |\n|--------|------|--------|\n" + ("\n".join(api_rows) if api_rows else "| — | No JSON API calls captured | — |") + "\n\n---\n\n*Generated by WebSkill Generator v1.0*\n"
    (skill_dir / "SKILL.md").write_text(content)
    log(f"SKILL.md written", "OK")


def write_actions_py(skill_dir: Path, config: dict, commands: list):
    template_path = Path(__file__).parent / "actions_template.py"
    if not template_path.exists():
        log(f"Template file not found: {template_path}", "ERR")
        return
    template = template_path.read_text()
    output = template.replace("__APP_NAME__", config["name"]).replace("__COMMANDS_JSON__", json.dumps(commands, indent=2))
    (skill_dir / "actions.py").write_text(output)
    log(f"actions.py written", "OK")


def write_config_json(skill_dir: Path, config: dict, pages: dict, commands: list, api_calls: list):
    output = {"name": config["name"], "url": config["url"],
              "auth_method": config.get("auth_method", "none"),
              "username": config.get("username", ""), "password": config.get("password", ""),
              "api_key": config.get("api_key", ""), "api_key_header": config.get("api_key_header", "Authorization"),
              "cookies": config.get("cookies", []),
              "pages_mapped": len(pages), "commands_count": len(commands),
              "api_endpoints_count": len(api_calls), "generated_at": datetime.now().isoformat()}
    (skill_dir / "config.json").write_text(json.dumps(output, indent=2))
    log(f"config.json written", "OK")


def interactive_setup() -> dict:
    print("\n🤖 WebSkill Generator — Interactive Setup\n")
    config = {}
    config["url"] = input("App URL: ").strip()
    config["name"] = input("Skill name (slug): ").strip() or slugify(config["url"])
    print("Auth method: none / form / apikey / cookie")
    config["auth_method"] = input("Auth method [form]: ").strip() or "form"
    if config["auth_method"] == "form":
        config["username"] = input("Username / Email: ").strip()
        config["password"] = input("Password: ").strip()
    elif config["auth_method"] == "apikey":
        config["api_key"] = input("API key: ").strip()
        config["api_key_header"] = input("Header name [Authorization]: ").strip() or "Authorization"
    elif config["auth_method"] == "cookie":
        raw = input("Cookies JSON: ").strip()
        try: config["cookies"] = json.loads(raw)
        except: config["cookies"] = []
    return config


def run(config: dict):
    app_name = slugify(config.get("name", "app"))
    config["name"] = app_name
    skill_dir = WEBSKILLS_DIR / app_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = SCREENSHOTS_DIR / app_name
    log(f"Starting WebSkill generation for: {app_name}", "STEP")
    api_tracker = APITracker()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
        page = ctx.new_page()
        api_tracker.attach(page)
        try:
            log("Step 1: Authenticating...", "STEP")
            AUTH_HANDLERS.get(config.get("auth_method", "none"), auth_none)(page, config)
            safe_screenshot(page, screenshot_dir, "00-post-login")
            log("Step 2: Exploring app structure...", "STEP")
            pages = explore_app(page, config, api_tracker, screenshot_dir)
            api_calls = api_tracker.get_summary()
            log(f"Step 3: Captured {len(api_calls)} API endpoints", "OK")
            log("Step 4: Deriving commands...", "STEP")
            commands = derive_commands(pages, api_calls)
            log(f"Derived {len(commands)} commands", "OK")
        except Exception as e:
            log(f"Exploration error: {e}", "ERR")
            traceback.print_exc()
            pages = {}; api_calls = []; commands = []
        finally:
            browser.close()
    log("Step 5: Writing skill files...", "STEP")
    write_skill_md(skill_dir, config, pages, commands, api_calls)
    write_actions_py(skill_dir, config, commands)
    write_config_json(skill_dir, config, pages, commands, api_calls)
    print(f"\n{'='*60}\n  ✅ WebSkill generated: {app_name}\n  📁 {skill_dir}\n  📄 Pages: {len(pages)} | ⚡ Commands: {len(commands)} | 🌐 APIs: {len(api_calls)}\n{'='*60}\n")
    return str(skill_dir)


def main():
    parser = argparse.ArgumentParser(description="WebSkill Generator")
    parser.add_argument("--url")
    parser.add_argument("--name")
    parser.add_argument("--auth-method", default="none", choices=["none", "form", "apikey", "cookie"])
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--api-key")
    parser.add_argument("--api-key-header", default="Authorization")
    parser.add_argument("--cookies")
    parser.add_argument("--interactive", "-i", action="store_true")
    parser.add_argument("--update")
    args = parser.parse_args()
    if args.update:
        run(json.loads(Path(args.update).read_text())); return
    if args.interactive or not args.url:
        config = interactive_setup()
    else:
        config = {"url": args.url, "name": args.name or slugify(args.url),
                  "auth_method": args.auth_method, "username": args.username or "",
                  "password": args.password or "", "api_key": args.api_key or "",
                  "api_key_header": args.api_key_header,
                  "cookies": json.loads(args.cookies) if args.cookies else []}
    run(config)


if __name__ == "__main__":
    main()
