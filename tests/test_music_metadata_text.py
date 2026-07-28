from app.services.library.music_search import _clean_display_text, _matches_search_query, _search_tokens


def test_clean_display_text_preserves_embedded_spacing():
    assert _clean_display_text("A Tribe Called Quest") == "A Tribe Called Quest"
    assert _clean_display_text("The Weeknd") == "The Weeknd"


def test_clean_display_text_does_not_humanize_compact_display_tags():
    assert _clean_display_text("BillieEilish") == "BillieEilish"


def test_search_tokens_include_humanized_compact_variants():
    tokens = _search_tokens("BillieEilish")
    assert "billie" in tokens
    assert "eilish" in tokens
    assert "billieeilish" in tokens


def test_search_matches_terms_anywhere_in_track_metadata():
    blob = "sweet home alabama lynyrd skynyrd second helping"
    assert _matches_search_query(blob, "Home")
    assert _matches_search_query(blob, "Alabama")
    assert _matches_search_query(blob, "home alabama")


def test_search_supports_star_and_question_mark_wildcards():
    blob = "sweet home alabama lynyrd skynyrd"
    assert _matches_search_query(blob, "sweet*alabama")
    assert _matches_search_query(blob, "alab?ma")
    assert not _matches_search_query(blob, "sweet*caroline")
