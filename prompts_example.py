# Example prompt shape — the real tuned prompt lives in private/prompts.py
NFT_LEGITIMACY_PROMPT = """\
Evaluate this NFT contract's legitimacy based on mint patterns, metadata, and deployer history.

Return ONLY a JSON object with this exact schema:
{
  "score": <int between 0 and 100>,
  "verdict": "LEGIT" | "SUSPICIOUS" | "LIKELY_RUG",
  "reason": "<brief explanation>"
}
"""
