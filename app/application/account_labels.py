"""Stable user-facing labels for Gate transport accounts."""


def account_label(*, alias: str, country_code: str | None = None) -> str:
    """Return RU/BY when known, otherwise a compact uppercase account alias."""

    country = (country_code or "").strip().upper()
    if country in {"RU", "BY"}:
        return country
    normalized_alias = alias.strip()
    suffix = normalized_alias.rsplit("_", 1)[-1].upper()
    if suffix in {"RU", "BY"}:
        return suffix
    return normalized_alias.upper()
