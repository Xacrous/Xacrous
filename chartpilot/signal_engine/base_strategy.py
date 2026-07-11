"""Signal contract and the abstract Strategy interface every strategy implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from chartpilot import DISCLAIMER
from chartpilot.ta_engine.indicators import IndicatorSet

Direction = Literal["long", "short"]
Mode = Literal["swing", "scalp"]


@dataclass
class Signal:
    """Matches the Signal object contract in the spec (Section 3)."""

    symbol: str
    timeframe: str
    mode: Mode
    strategy: str
    direction: Direction
    entry: float
    take_profit: float
    stop_loss: float
    confidence: int
    reward_risk_ratio: float
    rationale: list[str]
    historical_win_rate: float | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "mode": self.mode,
            "strategy": self.strategy,
            "direction": self.direction,
            "entry": self.entry,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "confidence": self.confidence,
            "reward_risk_ratio": self.reward_risk_ratio,
            "historical_win_rate": self.historical_win_rate,
            "rationale": self.rationale,
            "generated_at": self.generated_at,
            "disclaimer": self.disclaimer,
        }


class BaseStrategy(ABC):
    id: str
    display_name: str
    mode: Mode
    required_indicators: list[str]

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        """Evaluate the current candle set and return a Signal, or None if
        no setup currently qualifies under this strategy's rules."""
        raise NotImplementedError
