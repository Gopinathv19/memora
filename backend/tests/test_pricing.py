"""The operator's price list: versions, wildcards, units, validation."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.core.pricing import ModelRate, PriceList, load_price_list

SUPER = "nvidia/nemotron-3-super-120b-a12b"
PARSE = "nvidia/nemotron-parse"


def _list(*versions):
    return PriceList.model_validate({"versions": list(versions)})


def test_token_image_and_call_prices_add_up():
    rate = ModelRate(input_per_1m=0.30, output_per_1m=0.90, per_image=0.002, per_call=0.001)
    # 1M in * 0.30 + 0.5M out * 0.90 + 2 images * 0.002 + 0.001
    assert rate.cost(1_000_000, 500_000, 2) == pytest.approx(0.30 + 0.45 + 0.004 + 0.001)


def test_free_overrides_every_price():
    assert ModelRate(free=True, input_per_1m=99).cost(10**6, 10**6, 5) == 0.0


def test_newest_version_in_force_wins_and_future_versions_wait():
    prices = _list(
        {"effective_from": "2026-01-01", "providers": {"nebius": {SUPER: {"input_per_1m": 0.3}}}},
        {"effective_from": "2026-10-01", "providers": {"nebius": {SUPER: {"input_per_1m": 0.5}}}},
    )
    rate, since = prices.rate_for("nebius", SUPER, date(2026, 9, 24))
    assert (rate.input_per_1m, since) == (0.3, date(2026, 1, 1))
    rate, since = prices.rate_for("nebius", SUPER, date(2026, 10, 2))
    assert (rate.input_per_1m, since) == (0.5, date(2026, 10, 1))
    assert prices.rate_for("nebius", SUPER, date(2025, 12, 31)) is None


def test_wildcard_covers_every_model_of_a_provider():
    prices = _list({"effective_from": "2026-01-01", "providers": {"build-nvidia": {"*": {"free": True}}}})
    rate, _ = prices.rate_for("build-nvidia", PARSE, date(2026, 9, 24))
    assert rate.free


def test_unpriced_model_returns_none():
    prices = _list({"effective_from": "2026-01-01", "providers": {"nebius": {SUPER: {"input_per_1m": 0.3}}}})
    assert prices.rate_for("nebius", PARSE, date(2026, 9, 24)) is None
    assert prices.rate_for("other", SUPER, date(2026, 9, 24)) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"versions": [{"effective_from": "2026-01-01", "providers": {"nebius": {SUPER: {"input_per_1m": -1}}}}]},
        {"versions": [
            {"effective_from": "2026-01-01", "providers": {}},
            {"effective_from": "2026-01-01", "providers": {}},
        ]},
        {"versions": [{"effective_from": "not-a-date", "providers": {}}]},
    ],
)
def test_malformed_price_lists_are_rejected(bad):
    with pytest.raises(ValidationError):
        PriceList.model_validate(bad)


def test_missing_file_means_nothing_is_priced(tmp_path):
    assert load_price_list(tmp_path / "absent.json").versions == []


def test_the_shipped_price_list_is_valid():
    """The operator's file must always load; its prices are theirs to change."""
    from pathlib import Path

    shipped = Path(__file__).resolve().parents[1] / "pricing.json"
    prices = load_price_list(shipped)
    assert prices.versions, "pricing.json has no versions"
    assert json.loads(shipped.read_text())["_readme"]
    # Every configured model is priced (or explicitly free) on build.nvidia.com,
    # the provider used for development, so no call shows up as "unpriced".
    from app.core.config import get_settings

    for model in get_settings().llm_models.values():
        assert prices.rate_for("build-nvidia", model, date.today()) is not None, model


def test_specific_model_beats_the_wildcard():
    prices = _list({"effective_from": "2026-01-01", "providers": {"build-nvidia": {
        PARSE: {"per_image": 0.002},
        "*": {"per_call": 0.0005},
    }}})
    rate, _ = prices.rate_for("build-nvidia", PARSE, date(2026, 9, 24))
    assert rate.per_image == 0.002 and rate.per_call == 0
    rate, _ = prices.rate_for("build-nvidia", "nvidia/other-model", date(2026, 9, 24))
    assert rate.per_call == 0.0005
