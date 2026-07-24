"""Price provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import PriceResult


class PriceProvider(ABC):
    vendor_key: str = ""

    @abstractmethod
    def get_price(self, part_number: str, qty: int = 1) -> PriceResult:
        """Return a PriceResult. Never raise for 'not found' / site errors —
        set PriceResult.error instead."""
        raise NotImplementedError
