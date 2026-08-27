from .base import CANONICAL_COLUMNS
from . import suryaan, northbridge

PARTNERS = {
    "suryaan": suryaan,
    "northbridge": northbridge,
}

__all__ = ["CANONICAL_COLUMNS", "PARTNERS"]
