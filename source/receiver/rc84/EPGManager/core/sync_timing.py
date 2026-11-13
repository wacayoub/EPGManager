"""Explicit GMT+0 clocks and truthful source refresh timestamps. No I/O."""
from datetime import datetime, timezone
import math


def utc_now():
    return datetime.now(timezone.utc)


def utc_time(value):
    """Parse publication ISO dates or receipt epochs, never the machine zone."""
    try:
        if isinstance(value, bool) or value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            if not math.isfinite(value) or value <= 0:
                return None
            return datetime.fromtimestamp(value, timezone.utc)
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if result.tzinfo is None:
            # EPG-Scrapers generated_utc explicitly declares UTC for naive dates.
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def stamp(value):
    value = utc_time(value)
    return value.strftime("%d/%m %H:%M") if value is not None else "Unknown"


def source_refresh_text(metadata):
    metadata = metadata or {}
    return "%s / %s" % (stamp(metadata.get("generated")), stamp(metadata.get("checked")))


def preserve_sync_receipts(previous, current):
    """A metadata GET does not undo or fabricate a successful XML receipt."""
    result = {key: dict(value) for key, value in (current or {}).items()}
    for source_id, row in result.items():
        old = (previous or {}).get(source_id) or {}
        for field in ("local_synced", "local_path", "local_sync_error"):
            if field not in row and field in old:
                row[field] = old[field]
        # A newer successful receipt resolves an old error; a failed attempt
        # retains the last genuine success without calling it a new success.
        if ((current or {}).get(source_id, {}).get("local_synced") and
                "local_sync_error" not in (current or {}).get(source_id, {})):
            row.pop("local_sync_error", None)
    return result
