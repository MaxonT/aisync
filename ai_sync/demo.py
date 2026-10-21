"""aisync demo: a throwaway hub with a synthetic two-week journal, so `aisync status` has a story to tell.

What: five tools (claude-code, codex, cursor, windsurf, gemini-cli) working on three fictional repos
      (checkout-api, storefront-web, infra) for two weeks: commits, decisions, overlapping claims,
      a blocker that got unblocked, one still open, shared rules added along the way, and a
      rule conflict that the real sync engine detects (a copy and the truth edited differently).
Why:  a new user sees what the hub looks like after real use before wiring it into their own tools.
How:  `aisync demo [--dir D]`. Everything is written under D (default: a new temp dir); the real
      hub, tool configs and git config are never touched. All names and data are made up.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import collab, core

TOOLS = ("claude-code", "codex", "cursor", "windsurf", "gemini-cli")

BASE_RULES = [
    "- Before editing a repo other agents may touch, run `aisync claim`; exit code 3 means someone else holds those files.",
    "- After each unit of work, log one line with `aisync log`.",
    "- Every commit ends with an `Agent: <tool>` trailer.",
]

# (days ago, "HH:MM" or "-<minutes>" for today, agent, kind, project, summary, files, refs)
STORY = [
    (13, "09:12", "claude-code", "decision", "checkout-api",
     "move payment retries out of the request path into a queue worker; p99 on /pay was 2.8 s",
     ["docs/adr/0007-payment-retries.md"], {}),
    (13, "09:40", "claude-code", "commit", "checkout-api", "docs(adr): 0007 payment retries via queue worker",
     ["docs/adr/0007-payment-retries.md"], {}),
    (13, "11:05", "cursor", "claim", "storefront-web", "claimed: cart drawer redesign",
     ["src/components/CartDrawer.tsx", "src/components/CartDrawer.css"], {}),
    (13, "14:30", "cursor", "commit", "storefront-web", "feat(cart): slide-over drawer with line-item editing",
     ["src/components/CartDrawer.tsx", "src/components/CartDrawer.css", "src/hooks/useCart.ts"], {}),
    (13, "14:32", "cursor", "release", "storefront-web", "released: cart drawer redesign", [], {}),
    (13, "16:48", "codex", "dev_log", "infra",
     "split the staging VPC module so checkout-api and storefront-web get separate subnets",
     ["modules/vpc/main.tf", "envs/staging/main.tf"], {}),
    (12, "08:55", "gemini-cli", "dev_log", "infra", "terraform plan for staging is clean after the VPC split; no drift",
     [], {}),
    (12, "10:20", "claude-code", "claim", "checkout-api", "claimed: retry worker + idempotency keys",
     ["app/workers/", "app/payments/idempotency.py"], {}),
    (12, "13:02", "claude-code", "commit", "checkout-api", "feat(payments): retry worker with exponential backoff",
     ["app/workers/retry.py", "app/workers/__init__.py", "tests/test_retry_worker.py"], {}),
    (12, "15:40", "claude-code", "commit", "checkout-api", "feat(payments): idempotency keys on POST /pay",
     ["app/payments/idempotency.py", "app/api/pay.py", "tests/test_idempotency.py"], {}),
    (12, "15:41", "claude-code", "release", "checkout-api", "released: retry worker + idempotency keys", [], {}),
    (12, "17:15", "windsurf", "dev_log", "storefront-web",
     "replaced the hand-rolled price formatter with Intl.NumberFormat; fixes JPY showing two decimals",
     ["src/lib/money.ts", "src/lib/money.test.ts"], {}),
    (11, "09:30", "codex", "commit", "checkout-api", "test: contract tests for the /pay response schema",
     ["tests/contract/test_pay_schema.py", "tests/contract/pay.schema.json"], {}),
    (11, "10:05", "claude-code", "rule_change", "checkout-api",
     "shared rule added: never log full card numbers or tokens; mask to the last 4 digits", ["preferences.md"], {}),
    (11, "11:48", "cursor", "commit", "storefront-web", "fix(cart): drawer focus trap on Safari", [
        "src/components/CartDrawer.tsx"], {}),
    (11, "14:10", "git:alex", "commit", "storefront-web", "bump next to 14.2.5", ["package.json", "package-lock.json"], {}),
    (11, "16:22", "gemini-cli", "commit", "infra", "chore(staging): enable RDS performance insights",
     ["envs/staging/rds.tf"], {}),
    (10, "09:00", "windsurf", "claim", "storefront-web", "claimed: checkout page a11y pass", ["src/pages/checkout/"], {}),
    (10, "09:03", "cursor", "claim", "storefront-web", "claimed: promo code field on checkout",
     ["src/pages/checkout/PromoCode.tsx"], {"overlap_with": "windsurf"}),
    (10, "09:10", "cursor", "note", "storefront-web",
     "windsurf holds src/pages/checkout/; moved the promo field work to src/components/PromoCode.tsx instead",
     ["src/components/PromoCode.tsx"], {}),
    (10, "12:35", "windsurf", "commit", "storefront-web", "fix(a11y): labels and error announcements on checkout form",
     ["src/pages/checkout/AddressForm.tsx", "src/pages/checkout/CardForm.tsx"], {}),
    (10, "12:36", "windsurf", "release", "storefront-web", "released: checkout page a11y pass", [], {}),
    (10, "15:05", "cursor", "commit", "storefront-web", "feat(checkout): promo code component",
     ["src/components/PromoCode.tsx", "src/components/PromoCode.test.tsx"], {}),
    (10, "15:06", "cursor", "release", "storefront-web", "released: promo code field on checkout", [], {}),
    (9, "10:15", "codex", "blocked", "checkout-api",
     "integration tests need a payment-sandbox key in CI; the secret is missing from the staging environment",
     [".github/workflows/ci.yml"], {}),
    (9, "11:00", "claude-code", "dev_log", "checkout-api",
     "worked around the missing sandbox key locally with the recorded fixtures; CI still blocked",
     ["tests/fixtures/sandbox/"], {}),
    (9, "13:40", "gemini-cli", "commit", "infra", "feat(ci): OIDC role for checkout-api to read sandbox secrets",
     ["modules/ci-oidc/main.tf", "envs/staging/ci.tf"], {}),
    (9, "13:52", "gemini-cli", "unblocked", "checkout-api",
     "sandbox key now comes from the secrets manager through the new OIDC role; CI can read it",
     ["envs/staging/ci.tf"], {"blocked_id": "@blocked:checkout-api"}),
    (9, "14:30", "codex", "commit", "checkout-api", "ci: run integration tests against the payment sandbox",
     [".github/workflows/ci.yml"], {}),
    (8, "09:20", "windsurf", "decision", "storefront-web",
     "keep client-side price math only for display; totals always come from checkout-api /quote",
     ["docs/decisions/pricing.md"], {}),
    (8, "10:45", "claude-code", "commit", "checkout-api", "feat(api): /quote returns tax and discount lines",
     ["app/api/quote.py", "app/pricing/tax.py", "tests/test_quote.py"], {}),
    (8, "14:00", "cursor", "commit", "storefront-web", "feat(checkout): totals from /quote, remove local tax math",
     ["src/lib/quote.ts", "src/pages/checkout/Summary.tsx", "src/lib/tax.ts"], {}),
    (8, "16:30", "codex", "rule_change", "storefront-web",
     "shared rule added: every API change ships with a contract test in the same PR", ["preferences.md"], {}),
    (7, "09:05", "git:alex", "commit", "checkout-api", "hotfix: allow 0-amount quotes for gift cards",
     ["app/api/quote.py"], {}),
    (7, "09:40", "claude-code", "commit", "checkout-api", "test: contract test for zero-amount quotes",
     ["tests/contract/test_quote_schema.py"], {}),
    (7, "11:15", "gemini-cli", "dev_log", "infra", "prod plan shows 3 drifted security group rules; left untouched, opened ticket",
     [], {}),
    (7, "15:20", "codex", "commit", "storefront-web", "test(e2e): checkout happy path with Playwright",
     ["e2e/checkout.spec.ts", "playwright.config.ts"], {}),
    (6, "10:00", "cursor", "dev_log", "storefront-web", "Lighthouse on /checkout: LCP 3.1 s -> 1.9 s after lazy-loading the map",
     ["src/pages/checkout/AddressForm.tsx"], {}),
    (6, "13:25", "windsurf", "commit", "storefront-web", "perf: preload the payment iframe on cart open",
     ["src/components/CartDrawer.tsx", "src/lib/preload.ts"], {}),
    (6, "16:10", "claude-code", "decision", "checkout-api",
     "webhook handler becomes idempotent on event id; duplicates were double-capturing on retries",
     ["app/webhooks/payments.py"], {}),
    (5, "09:35", "claude-code", "commit", "checkout-api", "fix(webhooks): dedupe on provider event id",
     ["app/webhooks/payments.py", "app/models/webhook_event.py", "migrations/0031_webhook_event.py"], {}),
    (5, "11:50", "codex", "commit", "checkout-api", "test: replay duplicate webhooks in integration suite",
     ["tests/integration/test_webhook_replay.py"], {}),
    (5, "14:05", "gemini-cli", "commit", "infra", "fix(prod): import the 3 drifted security group rules into state",
     ["envs/prod/sg.tf"], {}),
    (4, "10:10", "cursor", "commit", "storefront-web", "feat(account): saved cards list",
     ["src/pages/account/Cards.tsx", "src/lib/api.ts"], {}),
    (4, "11:30", "windsurf", "rule_change", "storefront-web",
     "shared rule added: UI changes include a screenshot or a Playwright trace in the PR", ["preferences.md"], {}),
    (4, "15:45", "claude-code", "commit", "checkout-api", "feat(api): list and delete saved cards",
     ["app/api/cards.py", "tests/test_cards.py", "tests/contract/test_cards_schema.py"], {}),
    (3, "09:15", "codex", "dev_log", "checkout-api", "flaky test_retry_worker traced to a real clock; switched to a fake clock",
     ["tests/test_retry_worker.py"], {}),
    (3, "12:40", "git:alex", "commit", "infra", "raise staging RDS to db.t4g.medium", ["envs/staging/rds.tf"], {}),
    (3, "14:20", "gemini-cli", "dev_log", "infra", "staging RDS resize applied in a 4 min window; no errors in checkout-api logs",
     [], {}),
    (2, "10:00", "claude-code", "claim", "checkout-api", "claimed: refunds endpoint", ["app/api/refunds.py", "app/payments/"], {}),
    (2, "10:20", "codex", "claim", "checkout-api", "claimed: payment provider SDK upgrade", ["app/payments/provider.py"],
     {"overlap_with": "claude-code"}),
    (2, "10:24", "codex", "release", "checkout-api", "released: payment provider SDK upgrade", [], {}),
    (2, "10:25", "codex", "note", "checkout-api", "SDK upgrade waits until claude-code lands refunds; app/payments/ is claimed",
     [], {}),
    (2, "16:55", "claude-code", "commit", "checkout-api", "feat(api): partial refunds",
     ["app/api/refunds.py", "app/payments/refunds.py", "tests/test_refunds.py", "tests/contract/test_refunds_schema.py"], {}),
    (2, "16:56", "claude-code", "release", "checkout-api", "released: refunds endpoint", [], {}),
    (1, "09:30", "codex", "commit", "checkout-api", "chore(deps): payment provider SDK 4.2 -> 5.0",
     ["app/payments/provider.py", "pyproject.toml", "uv.lock"], {}),
    (1, "11:10", "cursor", "commit", "storefront-web", "feat(account): refund status on order page",
     ["src/pages/account/Order.tsx"], {}),
    (1, "14:45", "windsurf", "dev_log", "storefront-web", "visual diff on 14 checkout screens after the font swap: 0 regressions",
     [], {}),
    (1, "17:20", "gemini-cli", "decision", "infra", "prod changes go through a plan comment on the PR; no local applies",
     ["docs/runbooks/terraform.md"], {}),
    (0, "-190", "gemini-cli", "blocked", "infra",
     "prod plan needs a read-only role on the billing account; waiting for access", [], {}),
    (0, "-150", "claude-code", "dev_log", "checkout-api", "refund webhooks now update order status within one retry cycle",
     ["app/webhooks/payments.py"], {}),
    (0, "-95", "codex", "commit", "checkout-api", "test: refund webhook end-to-end in the sandbox",
     ["tests/integration/test_refund_webhook.py"], {}),
]

# Claims that are live right now: cursor and claude-code overlap on the checkout summary.
LIVE_CLAIMS = [
    ("windsurf", "storefront-web", ["src/styles/tokens.css", "src/components/Button.tsx"], "design tokens for dark mode", 95),
    ("cursor", "storefront-web", ["src/pages/checkout/"], "show refund credit on checkout summary", 110),
    ("claude-code", "storefront-web", ["src/pages/checkout/Summary.tsx"], "wire /quote credit lines into Summary", 120),
]


def _ts(now: datetime, days_ago: int, when: str) -> str:
    if when.startswith("-"):
        t = now - timedelta(minutes=int(when[1:]))
    else:
        hh, mm = map(int, when.split(":"))
        t = (now - timedelta(days=days_ago)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t.isoformat(timespec="seconds")


def _sha(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


def _rules_block(rules: list[str]) -> str:
    return "\n".join([core.BLOCK_START + " (shared by every AI tool; edit it in the hub's preferences.md) -->",
                      *BASE_RULES, "", collab.RULES_HEAD, *rules, core.BLOCK_END])


def _write_journal(cfg: core.Config, now: datetime) -> list[str]:
    rules, blocked_ids = [], {}
    for days_ago, when, agent, kind, project, summary, files, refs in sorted(
            STORY, key=lambda e: _ts(now, e[0], e[1])):
        ts = _ts(now, days_ago, when)
        refs = dict(refs)
        if kind == "commit":
            refs["commit"] = _sha(project, summary)
        if refs.get("blocked_id", "").startswith("@blocked:"):
            refs["blocked_id"] = blocked_ids.pop(refs["blocked_id"].split(":", 1)[1])
        ev = core.append_event(cfg, agent=agent, kind=kind, summary=summary, project=project,
                               files=files, refs=refs, ts=ts)
        if kind == "blocked":
            blocked_ids[project] = ev["id"]
        if kind == "rule_change":
            stamp = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
            rules.append(f"- {stamp} ({agent}, {project}): {summary.split(': ', 1)[1]}")
    return rules


def _write_runs(cfg: core.Config, now: datetime) -> None:
    """Sync-cycle history: event-driven cycles during working hours, a short push outage on day 6.
    Each cycle harvests exactly the journal's commits made since the previous cycle."""
    rnd = random.Random(14)
    commits = sorted(e["ts"] for e in core.read_events(cfg) if e["kind"] == "commit")
    lines, prev = [], ""
    for days_ago in range(13, -1, -1):
        day = now - timedelta(days=days_ago)
        t = day.replace(hour=8, minute=rnd.randint(30, 59), second=0, microsecond=0)
        while t.hour < 19 and t < now - timedelta(minutes=5):
            failed = days_ago == 6 and 15 <= t.hour < 16
            steps = {name: {"ok": True, "ms": rnd.randint(2, 40), "result": {}}
                     for name in ("harvest", "rules", "distribute", "render", "progress")}
            stamp = t.isoformat(timespec="seconds")
            steps["harvest"]["result"] = {"events": sum(prev < c <= stamp for c in commits), "projects": 3, "errors": []}
            prev = stamp
            steps["distribute"]["result"] = {"updated": [], "conflicts": []}
            steps["flush"] = ({"ok": False, "ms": 20000, "error": "GitError: git push: timeout after 20.0s"} if failed
                              else {"ok": True, "ms": rnd.randint(60, 420), "result": {}})
            lines.append({"ts": stamp, "reason": rnd.choice(("event", "event", "event", "reconcile")),
                          "steps": steps, "ok": not failed, "ms": sum(s["ms"] for s in steps.values())})
            t += timedelta(minutes=rnd.randint(25, 70))
    cfg.state.mkdir(parents=True, exist_ok=True)
    (cfg.state / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))


def _write_hook_log(cfg: core.Config, now: datetime) -> None:
    rnd = random.Random(24)
    recs = []
    for tool, n in (("claude-code", 64), ("codex", 41), ("cursor", 37)):
        for i in range(n):
            t = now - timedelta(minutes=rnd.randint(5, 23 * 60))
            injected = rnd.random() < 0.18
            recs.append({"ts": t.isoformat(timespec="seconds"), "tool": tool, "event": "PostToolUse",
                         "session": f"{tool[:2]}-{rnd.randint(1000, 9999)}", "ok": True,
                         "events": rnd.randint(1, 4) if injected else 0, "rules": 0,
                         "chars": rnd.randint(300, 900) if injected else 0, "ms": rnd.randint(9, 48)})
    recs.sort(key=lambda r: r["ts"])
    (cfg.state / "hooks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


def build(root: Path | None = None) -> Path:
    os.environ.setdefault("AISYNC_NO_NOTIFY", "1")         # the demo never pops desktop notifications
    root = Path(root) if root else Path(tempfile.mkdtemp(prefix="aisync-demo-"))
    if (root / "journal.jsonl").exists():
        raise ValueError(f"{root} already holds a hub; pick an empty --dir")
    tools_dir = root / "tool-files"
    targets = {"claude-code": tools_dir / "claude" / "CLAUDE.md", "codex": tools_dir / "codex" / "AGENTS.md",
               "windsurf": tools_dir / "windsurf" / "global_rules.md"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(
        f"roster = {json.dumps(list(TOOLS))}\n" + "".join(
            f'\n[[target]]\nlabel = "{label}"\npath = "{path}"\n' for label, path in targets.items()))
    (root / ".gitignore").write_text("backups/\ngithooks/\nstate/*\n!state/current.json\nSTOP\n")
    (root / "journal.jsonl").write_text("")
    cfg = core.load_config(root)
    now = datetime.now().astimezone().replace(microsecond=0)

    rules = _write_journal(cfg, now)
    block = _rules_block(rules)
    (root / "preferences.md").write_text(f"# Shared rules (source of truth)\n\n{block}\n")
    for label, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {label} rules\n\nProject notes for {label} live above the shared block.\n\n{block}\n")
    _write_runs(cfg, now)
    _write_hook_log(cfg, now)
    core.run_cycle(cfg, "demo")                             # all copies in sync, hashes recorded

    for agent, project, files, summary, ttl in LIVE_CLAIMS:
        collab.claim(cfg, agent=agent, project=project, summary=summary, files=files, ttl_min=ttl)
    # The conflict: a new rule lands in the truth while someone hand-edits the codex copy differently.
    collab.rule_add(cfg, agent="cursor", project="storefront-web",
                    text="feature flags are removed within two weeks of full rollout",
                    reason="three stale flags were found in checkout")
    codex = targets["codex"]
    codex.write_text(codex.read_text().replace(
        BASE_RULES[1], BASE_RULES[1] + "\n- Prefer small PRs: under 400 changed lines unless it is a rename."))
    core.run_cycle(cfg, "demo")
    return root
