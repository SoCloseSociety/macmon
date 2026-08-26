"""Tests for the pure functions in macmon_core.utils.

Everything here is platform-independent: no subprocess, no network, no
filesystem access beyond the module import itself.
"""
import pytest

from macmon_core.utils import (
    _applescript_escape,
    categorize_process,
    format_duration,
    format_size,
)


# -- format_size ----------------------------------------------------------

class TestFormatSize:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0.0 B"),
            (1, "1.0 B"),
            (1023, "1023.0 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (1024**3, "1.0 GB"),
            (1024**4, "1.0 TB"),
        ],
    )
    def test_exact_strings(self, value, expected):
        assert format_size(value) == expected

    def test_negative_is_zero_bytes(self):
        assert format_size(-1) == "0 B"
        assert format_size(-(1024**3)) == "0 B"

    def test_petabyte_scale(self):
        assert format_size(1024**5) == "1.0 PB"
        # Values past the unit table stay in PB rather than overflowing
        assert format_size(3 * 1024**5) == "3.0 PB"
        result = format_size(1024**6)
        assert result.endswith(" PB")


# -- format_duration ------------------------------------------------------

class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(30) == "30s"

    def test_minutes_no_rounding_bug(self):
        # 90s is 1m 30s. A historical bug rounded this up to "2m 30s"; lock
        # the fixed behavior.
        assert format_duration(90) == "1m 30s"

    def test_hours(self):
        assert format_duration(3661) == "1h 1m"

    def test_days(self):
        assert format_duration(90000) == "1d 1h"


# -- categorize_process ---------------------------------------------------

class TestCategorizeProcess:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Google Chrome", "browser"),
            ("node", "node"),
            ("python3.11", "python"),
            ("Code Helper (Renderer)", "ide"),
            ("ollama", "llm"),
            ("some-random-daemon", "other"),
        ],
    )
    def test_categories(self, name, expected):
        assert categorize_process(name) == expected

    def test_short_keyword_exact_match(self):
        # "code" is in SHORT_KEYWORDS: exact name matches...
        assert categorize_process("code") == "ide"
        # ...and prefix forms match ("code " / "code-")...
        assert categorize_process("code --serve") == "ide"
        # ...but a substring hit inside another word must NOT match.
        assert categorize_process("barcode-scanner") == "other"

    def test_case_insensitive(self):
        assert categorize_process("FIREFOX") == "browser"


# -- _applescript_escape --------------------------------------------------

class TestApplescriptEscape:
    def test_escapes_quotes_and_backslashes(self):
        assert _applescript_escape('say "hi"') == 'say \\"hi\\"'
        assert _applescript_escape("a\\b") == "a\\\\b"
        # Backslashes are escaped before quotes, so a mixed string does not
        # double-escape the backslash introduced for the quote.
        assert _applescript_escape('a"b\\c') == 'a\\"b\\\\c'

    def test_plain_string_unchanged(self):
        assert _applescript_escape("hello world") == "hello world"
