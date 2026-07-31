import pytest

from app.models.enums import DataSourceKind
from app.services.data_import.merge import (
    MergeCandidate,
    account_entity_key,
    audience_entity_key,
    benchmark_entity_key,
    choose_winner,
    content_entity_key,
    iter_present_fields,
    source_priority,
)


@pytest.mark.parametrize(
    ("builder", "arguments", "expected"),
    [
        (account_entity_key, (12,), "account:12"),
        (content_entity_key, (12, 98), "account:12:content:98"),
        (
            audience_entity_key,
            (12, "city:tier", "new/user"),
            "account:12:audience:city%3Atier:new%2Fuser",
        ),
        (
            benchmark_entity_key,
            (12, "track:median"),
            "account:12:benchmark:track%3Amedian",
        ),
    ],
)
def test_business_keys_are_stable_and_escape_user_controlled_segments(
    builder,
    arguments,
    expected,
):
    assert builder(*arguments) == expected


def test_present_fields_ignore_missing_values_but_preserve_explicit_zero_and_false():
    normalized = {
        "missing": None,
        "blank": "  ",
        "dash": "-",
        "marker": "N/A",
        "zero_int": 0,
        "zero_float": 0.0,
        "false": False,
        "title": "Glass film",
    }

    assert list(iter_present_fields(normalized)) == [
        ("zero_int", 0),
        ("zero_float", 0.0),
        ("false", False),
        ("title", "Glass film"),
    ]


def test_source_priority_matches_the_approved_provenance_order():
    assert source_priority(DataSourceKind.OFFICIAL_API) == 400
    assert source_priority(DataSourceKind.PLATFORM_EXPORT) == 300
    assert source_priority(DataSourceKind.SCREENSHOT_VERIFIED) == 200
    assert source_priority(DataSourceKind.MANUAL_ENTRY) == 100


def test_higher_priority_source_wins_even_when_it_was_confirmed_earlier():
    official = MergeCandidate(
        value=80,
        source_kind=DataSourceKind.OFFICIAL_API,
        confirmed_sequence=4,
        observation_id=10,
    )
    later_manual = MergeCandidate(
        value=100,
        source_kind=DataSourceKind.MANUAL_ENTRY,
        confirmed_sequence=20,
        observation_id=11,
    )

    assert choose_winner([later_manual, official]) is official


def test_persisted_source_priority_is_used_when_projection_builds_candidates():
    historically_ranked = MergeCandidate(
        value=80,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        source_priority=500,
        confirmed_sequence=4,
        observation_id=10,
    )
    official = MergeCandidate(
        value=100,
        source_kind=DataSourceKind.OFFICIAL_API,
        source_priority=400,
        confirmed_sequence=20,
        observation_id=11,
    )

    assert choose_winner([official, historically_ranked]) is historically_ranked


def test_later_confirmation_wins_within_the_same_source_priority():
    earlier = MergeCandidate(
        value=80,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        confirmed_sequence=4,
        observation_id=10,
    )
    later = MergeCandidate(
        value=0,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        confirmed_sequence=5,
        observation_id=11,
    )

    assert choose_winner([later, earlier]) is later
    assert choose_winner([later, earlier]).value == 0


def test_observation_id_breaks_an_exact_tie_and_inactive_candidates_are_ignored():
    first = MergeCandidate(
        value=8,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        confirmed_sequence=5,
        observation_id=10,
    )
    second = MergeCandidate(
        value=9,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        confirmed_sequence=5,
        observation_id=11,
    )
    inactive = MergeCandidate(
        value=999,
        source_kind=DataSourceKind.OFFICIAL_API,
        confirmed_sequence=99,
        observation_id=12,
        active=False,
    )

    assert choose_winner([inactive, first, second]) is second
    assert choose_winner([inactive]) is None
    assert choose_winner([]) is None
