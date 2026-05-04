import re
import subprocess
from pathlib import Path

_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def normalize_date(date_str: str | None) -> str | None:
    """Normalize a date string to mm/dd/yyyy. Returns input unchanged if unparseable."""
    if not date_str:
        return date_str
    s = date_str.strip()
    if not s:
        return date_str

    # Already mm/dd/yyyy (with or without zero-padding)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"

    # ISO YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"

    # YYYY-MM
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        return f"{m.group(2)}/01/{m.group(1)}"

    # YYYY/MM/DD
    m = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", s)
    if m:
        return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"

    # YYYY/MM
    m = re.match(r"^(\d{4})/(\d{2})$", s)
    if m:
        return f"{m.group(2)}/01/{m.group(1)}"

    # Just YYYY — can't expand to mm/dd/yyyy, return as-is
    if re.match(r"^\d{4}$", s):
        return s

    # "Month DD, YYYY" or "Month DD YYYY"
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mon = _MONTH_NAMES.get(m.group(1).lower())
        if mon:
            return f"{mon:02d}/{int(m.group(2)):02d}/{m.group(3)}"

    # "Month YYYY"
    m = re.match(r"^([A-Za-z]+)\s+(\d{4})$", s)
    if m:
        mon = _MONTH_NAMES.get(m.group(1).lower())
        if mon:
            return f"{mon:02d}/01/{m.group(2)}"

    # "DD Month YYYY" (European)
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", s)
    if m:
        mon = _MONTH_NAMES.get(m.group(2).lower())
        if mon:
            return f"{mon:02d}/{int(m.group(1)):02d}/{m.group(3)}"

    # m/d/yy (two-digit year)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", s)
    if m:
        yr = int(m.group(3))
        full_year = str(2000 + yr if yr < 50 else 1900 + yr)
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{full_year}"

    return s


def get_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=Path(__file__).parent.parent,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"
