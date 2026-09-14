"""Daily, neutral community guidance for the Telegram channel."""

from datetime import datetime, timezone
from random import choice

import checkpoint
from notifier import asend


PULSES = (
    "Verify the contract before acting.",
    "A mint is a signal, not a guarantee.",
    "Liquidity, holders, and history matter.",
    "Patience protects capital.",
    "Check distribution, not just mint count.",
    "Independent research beats crowd momentum.",
    "A good entry starts with a clear risk limit.",
    "Strong communities ask better questions.",
    "Data first, conviction second.",
    "Avoid urgency; review the evidence.",
    "Transparency is a positive signal.",
    "No alert replaces your own judgment.",
    "Diversification keeps one mint from defining the day.",
    "Watch the wallets, not only the headlines.",
    "A healthy mint has more than hype behind it.",
    "Preserve capital; opportunities return.",
    "Think in probabilities, not promises.",
    "Research before reaction.",
    "Trust signals should be verified, not assumed.",
    "Every trade needs a reason and an exit plan.",
    "Check contract permissions before connecting your wallet.",
    "Unknown approvals can create unnecessary risk.",
    "A verified contract is safer to inspect, not automatically safe to use.",
    "Never let FOMO replace wallet security.",
)


PULSE_SECTION = "community_pulse_days"


def community_pulse() -> str:
    """Return one clearly labeled standalone Community pulse message."""
    return f"✨ <b>Community pulse</b>\n<i>{choice(PULSES)}</i>"


async def send_daily_community_pulse(now: datetime | None = None) -> bool:
    """Send at most one pulse per UTC day, including after a restart."""
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    if checkpoint.was_seen(PULSE_SECTION, today):
        return False

    await asend(community_pulse())
    # Record only after Telegram accepts the message, so a failed send can retry.
    checkpoint.mark_seen(PULSE_SECTION, today, flush_now=True)
    print(f"[CommunityPulse] Sent daily pulse for {today}")
    return True
