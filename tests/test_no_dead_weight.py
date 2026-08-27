"""Guards against the dead weight removed in the codebase audit.

Each removal below was justified by a live probe, recorded here so a future change
cannot quietly reintroduce the same cost.

1. calendar_tracker.py: 286 lines, imported by nothing. Its two scrapers
   duplicated live_drops.py (NFTCalendar) and floor.py (OpenSea collections), and
   check_calendar() was never scheduled. Its is_junk/is_within_age helpers were used
   only by itself and its own tests; drops.py has an independent name filter.

2. Alchemy: probed on all five configured networks. ethereum/base/arbitrum
   returned HTTP 429 "Monthly capacity limit exceeded"; polygon and optimism
   returned HTTP 403 "not enabled for this app", meaning they never worked at all.
   The code used no Alchemy enhanced APIs (no getAssetTransfers, no nft/v3, no
   alchemy_* methods), only standard JSON-RPC that public endpoints serve with
   byte-identical results.

3. schedule==1.2.2: declared in requirements.txt, imported by no module. The bot
   schedules work with asyncio loops in bot.py.
"""
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _production_sources():
    """Every production .py file (excludes tests and scripts)."""
    skip = {"tests", "scripts", "private", "local_docs"}
    for path in REPO.glob("*.py"):
        if path.parent.name not in skip:
            yield path


# ── calendar_tracker removal ─────────────────────────────────────────────────

def test_calendar_tracker_module_is_gone():
    assert not (REPO / "calendar_tracker.py").exists(), (
        "calendar_tracker.py was removed: 286 lines, zero importers, duplicated "
        "live_drops.py and floor.py"
    )


def test_nothing_imports_calendar_tracker():
    """No module may import the deleted module.

    Looks for import statements rather than the bare name: live_drops.py documents
    where its junk filters came from, and that provenance note is worth keeping.
    """
    offenders = []
    for path in _production_sources():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "calendar_tracker" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, f"calendar_tracker imported again: {offenders}"


def test_live_drops_survives_as_the_single_nftcalendar_scraper():
    """The /live command depends on it and it returned real drops when probed."""
    assert (REPO / "live_drops.py").exists()
    commands = (REPO / "commands.py").read_text(encoding="utf-8")
    assert "from live_drops import" in commands


# ── Alchemy removal ──────────────────────────────────────────────────────────

def test_no_module_references_alchemy():
    """No module may build or call an Alchemy endpoint.

    Checks for actual usage (the URL host and the config name), not the word
    itself: drops.py carries a comment explaining why the integration was removed,
    and that comment is documentation worth keeping.
    """
    markers = ("g.alchemy.com", "ALCHEMY_API_KEY", "_ALCHEMY_SUBDOMAINS")
    offenders = {}
    for path in _production_sources():
        body = path.read_text(encoding="utf-8")
        hits = [m for m in markers if m in body]
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        f"Alchemy reintroduced: {offenders}. It returned 429 on "
        "ethereum/base/arbitrum and 403 'not enabled' on polygon/optimism, and no "
        "enhanced API was ever used."
    )


def test_alchemy_key_is_not_a_required_config_name():
    """ALCHEMY_API_KEY must not appear in the guarded private-config imports.

    Leaving it there means a private config that drops the key breaks the whole
    import statement and silently swaps in public defaults.
    """
    for path in _production_sources():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "import" in line and "ALCHEMY_API_KEY" in line:
                pytest.fail(f"{path.name} still imports ALCHEMY_API_KEY: {line.strip()}")


def test_rpc_lists_contain_no_alchemy_urls():
    drops_src = (REPO / "drops.py").read_text(encoding="utf-8")
    assert "g.alchemy.com" not in drops_src


# ── dependency hygiene ───────────────────────────────────────────────────────

def _declared_requirements():
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==")[0].split(">=")[0].split("[")[0].strip()
        names.append(name)
    return names


def test_schedule_is_not_a_dependency():
    assert "schedule" not in _declared_requirements(), (
        "schedule was removed: nothing imports it, bot.py uses asyncio loops"
    )


# ── dead chain removal ───────────────────────────────────────────────────────

def test_zora_chain_is_removed():
    """Zora produced zero NFT mints across a 20,000 block probe (10/10 windows
    queried successfully), so scanning it every cycle was pure cost."""
    drops_src = (REPO / "drops.py").read_text(encoding="utf-8")
    assert '"zora"' not in drops_src
    commands_src = (REPO / "commands.py").read_text(encoding="utf-8")
    assert '"zora"' not in commands_src


def test_robinhood_chain_is_kept():
    """Robinhood looked dead in the logs but is not: the same probe found 30,953
    mint events in 20,000 blocks. The logs showed zero because the persisted
    watermark was ~2.3 million blocks behind the chain tip, which is a checkpoint
    problem, not a dead chain. Removing it would have deleted a working chain.
    """
    drops_src = (REPO / "drops.py").read_text(encoding="utf-8")
    assert '"robinhood"' in drops_src


def test_every_declared_dependency_is_actually_imported():
    """A manifest that overstates its needs makes installs slower and audits harder."""
    import_names = {
        "requests": "requests",
        "python-telegram-bot": "telegram",
        "python-dotenv": "dotenv",
        "curl_cffi": "curl_cffi",
        "beautifulsoup4": "bs4",
        "google-genai": "google",
        "svglib": "svglib",
        "reportlab": "reportlab",
        "cairosvg": "cairosvg",
    }
    all_source = "\n".join(
        p.read_text(encoding="utf-8") for p in _production_sources()
    )
    unused = []
    for dep in _declared_requirements():
        module = import_names.get(dep)
        if module is None:
            continue
        # Match both "import x" and "from x import y"; a bare substring check on
        # "import x" alone misses every from-import and flags healthy deps.
        if f"import {module}" not in all_source and f"from {module}" not in all_source:
            unused.append(dep)
    assert not unused, f"declared but never imported: {unused}"
