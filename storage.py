"""JSON-file persistence for the set of applied object ids."""

import json
import logging
import os

_APPLIED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "applied.json")

logger = logging.getLogger(__name__)


def load_applied() -> set[str]:
    """Load the set of applied object ids from applied.json.

    Returns an empty set if the file is missing or corrupt.
    """
    try:
        with open(_APPLIED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(str(x) for x in data)
        logger.warning("applied.json has unexpected format, starting fresh")
        return set()
    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Could not read applied.json (%s), starting fresh", e)
        return set()


def save_applied(ids: set[str]) -> None:
    """Persist the set of applied object ids to applied.json."""
    try:
        with open(_APPLIED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(ids), f, indent=2)
    except Exception as e:
        logger.error("Failed to save applied.json: %s", e)
