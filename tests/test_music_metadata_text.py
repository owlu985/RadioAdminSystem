from app.services.library.music_search import _clean_display_text, _search_tokens


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
