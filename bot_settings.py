"""Safe resolution of optional settings from the private config.

Why this exists: bot.py and the scanner modules load their private config through
a guarded try/except that falls back to the public config module when the private
one is absent.

If a name is added to that guarded statement and a user's private config does not
define it, the whole statement fails and the fallback replaces every value,
including real API keys, with public defaults. The failure is silent: no crash,
just a bot quietly running on placeholders.

So a NEW optional setting must never be bolted onto that guarded statement. Resolve
it here instead: read it off the private config when present, else fall back to a
default. One missing name can then only affect that one setting.
"""


def resolve(name: str, default, source=None):
    """Return source.<name> if the attribute exists, otherwise default.

    `source` is the already-imported private config module (or any object). Passing
    None models "no private config on this machine" and yields the default.
    """
    if source is None:
        return default
    return getattr(source, name, default)


def _try_import_private():
    """Best-effort import of the private config, or None if it is absent.

    Deliberately isolated so a missing private config degrades to defaults instead
    of taking anything else down with it.
    """
    try:
        import private.config_live as private_config
        return private_config
    except Exception:
        return None


def get_bool(name: str, default: bool) -> bool:
    """Resolve a boolean setting: private config first, then environment.

    Environment values are parsed leniently: true/1/yes/on (any case) are True,
    everything else is False.
    """
    private_config = _try_import_private()
    if private_config is not None and hasattr(private_config, name):
        return bool(getattr(private_config, name))

    import os
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")
