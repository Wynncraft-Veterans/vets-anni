"""Pure-logic settings tests — no DB, no app, fast.

The ally-tag parser is the one bit of settings logic with real edge cases
(env arrives as a comma string, tags are case-SENSITIVE on Wynncraft, empty
=> no ally guild). ``_env_file=None`` keeps each case hermetic from any local
.env or process env.
"""

from __future__ import annotations

from app.settings import Settings


def _s(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_ally_tags_split_trims_and_preserves_case():
    s = _s(ally_guild_tags="SSNE, tcm ,VSI,")
    assert s.ally_guild_tags == ["SSNE", "tcm", "VSI"]
    assert s.ally_guild_tag_set == frozenset({"SSNE", "tcm", "VSI"})


def test_ally_tags_accepts_a_list_too():
    assert _s(ally_guild_tags=["A", " B "]).ally_guild_tags == ["A", "B"]


def test_empty_ally_tags_means_no_ally_guild():
    s = _s(ally_guild_tags="")
    assert s.ally_guild_tags == []
    assert s.ally_guild_tag_set == frozenset()


def test_db_url_is_derived_from_the_path():
    assert _s(anni_db_path="./data/x.db").db_url == "sqlite://./data/x.db"


def test_enabled_regions_default_is_wynns_current_proxies():
    # Wynn only runs AS/EU/NA today — that's the default offer set.
    assert _s().enabled_regions == ["AS", "EU", "NA"]


def test_enabled_regions_split_trims_and_uppercases():
    s = _s(enabled_regions=" as , eu ,NA, ")
    assert s.enabled_regions == ["AS", "EU", "NA"]
    assert _s(enabled_regions=["oc", " sa "]).enabled_regions == ["OC", "SA"]


def test_empty_enabled_regions_means_no_picker():
    assert _s(enabled_regions="").enabled_regions == []


def test_defaults():
    s = _s()
    assert s.ally_guild_tags == ["SSNE", "TCM", "VSI", "BELL"]
    assert s.returners_guild_name == "Returners"
    assert s.enabled_regions == ["AS", "EU", "NA"]
    assert s.debug is False
