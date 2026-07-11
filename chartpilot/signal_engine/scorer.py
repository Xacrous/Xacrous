"""0-100 confidence scoring shared by every strategy.

Each strategy scores a small number of weighted factors (trend/regime fit,
trigger strength, location/confluence, volume confirmation — Section 4) and
this module sums them into a single confidence figure while keeping every
factor's own sub-score visible in the rationale, so confidence is always
inspectable rather than a black box (Section 8's mitigation for signal-
accuracy risk).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoreFactor:
    description: str
    score: float
    max_score: float

    def __post_init__(self) -> None:
        if self.score < 0 or self.score > self.max_score:
            raise ValueError(
                f"score {self.score} out of range [0, {self.max_score}] for {self.description!r}"
            )


def total_confidence(factors: list[ScoreFactor]) -> int:
    total = sum(f.score for f in factors)
    return max(0, min(100, round(total)))


def rationale_lines(factors: list[ScoreFactor]) -> list[str]:
    return [f"{f.description} ({f.score:.0f}/{f.max_score:.0f})" for f in factors]
