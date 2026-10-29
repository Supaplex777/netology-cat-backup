import pytest

from cataas_api import (
    InvalidTextError,
    build_cat_url,
    extension_for,
    make_file_stem,
    validate_text,
)


def test_build_url_simple_text():
    assert build_cat_url("hello") == "https://cataas.com/cat/says/hello"


def test_build_url_encodes_spaces_and_cyrillic():
    url = build_cat_url("Привет мир")
    assert url == ("https://cataas.com/cat/says/"
                   "%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%20%D0%BC%D0%B8%D1%80")
    assert " " not in url


def test_build_url_encodes_special_characters():
    url = build_cat_url("a/b?c#d&e")
    assert url.endswith("/cat/says/a%2Fb%3Fc%23d%26e")


def test_build_url_custom_base():
    assert build_cat_url("hi", "http://localhost/") == (
        "http://localhost/cat/says/hi")


@pytest.mark.parametrize("text", ["", "   ", "\t\n", None])
def test_empty_text_is_rejected(text):
    with pytest.raises(InvalidTextError):
        build_cat_url(text)


def test_too_long_text_is_rejected():
    with pytest.raises(InvalidTextError):
        validate_text("x" * 101)


def test_validate_collapses_spaces():
    assert validate_text("  Hello   big\tworld ") == "Hello big world"


def test_file_stem_keeps_cyrillic():
    assert make_file_stem("Привет, котик!") == "Привет, котик!"


def test_file_stem_replaces_forbidden_characters():
    assert make_file_stem('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_file_stem_strips_dots_and_spaces():
    assert make_file_stem(" ...hello... ") == "hello"


@pytest.mark.parametrize("text", ["///", "...", "?*"])
def test_file_stem_empty_after_cleanup(text):
    with pytest.raises(InvalidTextError):
        make_file_stem(text)


@pytest.mark.parametrize("mime, ext", [
    ("image/jpeg", ".jpg"),
    ("image/png", ".png"),
    ("image/gif", ".gif"),
    ("IMAGE/PNG; charset=binary", ".png"),
    ("application/octet-stream", ".jpg"),
    (None, ".jpg"),
])
def test_extension_for_mime(mime, ext):
    assert extension_for(mime) == ext
