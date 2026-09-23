"""The operator's price list: what each model call costs Memora.

Prices are service configuration, not tenant data. They live in a versioned
JSON file in the backend (PRICING_FILE, default backend/pricing.json), edited
by whoever runs Memora and reviewed in git. No API route or console screen can
change them; console users and API credentials only ever see the resulting
costs.

Each version has an `effective_from` date. A call is priced by the newest
version in force when it was made, and the rate applied is copied onto its
usage row, so changing a price never rewrites past costs.
"""

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings


class ModelRate(BaseModel):
    """How one model is billed. Any mix of the four prices may be set."""

    input_per_1m: float = Field(default=0.0, ge=0)
    output_per_1m: float = Field(default=0.0, ge=0)
    # For page images and pictures: Nemotron-Parse reports ~5 input tokens
    # for a whole page, so token prices alone would undercount it.
    per_image: float = Field(default=0.0, ge=0)
    per_call: float = Field(default=0.0, ge=0)
    free: bool = False

    def cost(self, prompt_tokens: int, completion_tokens: int, images: int) -> float:
        if self.free:
            return 0.0
        return round(
            prompt_tokens * self.input_per_1m / 1_000_000
            + completion_tokens * self.output_per_1m / 1_000_000
            + images * self.per_image
            + self.per_call,
            6,
        )


class PriceVersion(BaseModel):
    effective_from: date
    # provider -> model id (or "*" for every model of that provider) -> rate
    providers: dict[str, dict[str, ModelRate]]


class PriceList(BaseModel):
    versions: list[PriceVersion]

    @model_validator(mode="after")
    def _newest_first(self) -> "PriceList":
        dates = [v.effective_from for v in self.versions]
        if len(dates) != len(set(dates)):
            raise ValueError("two price versions share an effective_from date")
        self.versions.sort(key=lambda v: v.effective_from, reverse=True)
        return self

    def rate_for(
        self, provider: str, model: str, at: datetime | date | None = None
    ) -> tuple[ModelRate, date] | None:
        """The rate in force for this model at `at` (default: today)."""
        day = (at.date() if isinstance(at, datetime) else at) or date.today()
        for version in self.versions:  # newest first
            if version.effective_from > day:
                continue
            models = version.providers.get(provider, {})
            rate = models.get(model) or models.get("*")
            return (rate, version.effective_from) if rate else None
        return None


def load_price_list(path: str | Path) -> PriceList:
    path = Path(path)
    if not path.exists():
        # No file means nothing is priced; every call is recorded at $0.
        return PriceList(versions=[])
    with path.open(encoding="utf-8") as handle:
        return PriceList.model_validate(json.load(handle))


@lru_cache
def get_price_list() -> PriceList:
    return load_price_list(get_settings().pricing_file)


def price_call(
    provider: str,
    model: str,
    role: str,
    prompt_tokens: int,
    completion_tokens: int,
    at: datetime | None = None,
) -> tuple[float, dict | None]:
    """Cost of one model call, and the rate snapshot to store with it.

    Layout and vision calls each send one image; the extract call sends none.
    An unpriced model costs $0 and returns no snapshot, so it stands out.
    """
    found = get_price_list().rate_for(provider, model, at)
    if found is None:
        return 0.0, None
    rate, effective_from = found
    images = 0 if role == "extract" else 1
    snapshot = {**rate.model_dump(), "effective_from": effective_from.isoformat()}
    return rate.cost(prompt_tokens, completion_tokens, images), snapshot
