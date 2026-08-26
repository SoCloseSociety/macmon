"""Tests for the size parsers in macmon_core.disk, macmon_core.gc and macmon_core.sentinel.

Contracts verified against the source:
- disk._parse_size is BINARY (1024-based) and accepts "50MB", "500K", bare
  ints; unparseable input raises typer.Exit(code=1).
- gc._parse_docker_size is DECIMAL (1000-based), matching docker's output,
  strips a trailing "(NN%)" and returns int bytes (0 for garbage).
- sentinel._SIZE_RE / _SIZE_FACTOR_GB parse `ollama ps` sizes (decimal
  HumanBytes) into GB.
"""
import re

import pytest
import typer

from macmon_core.disk import _parse_size
from macmon_core.gc import _parse_docker_size
from macmon_core.sentinel import _SIZE_FACTOR_GB, _SIZE_RE


# -- disk._parse_size (binary, 1024-based) --------------------------------

class TestDiskParseSize:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("50MB", 50 * 1024**2),
            ("500K", 500 * 1024),
            ("1G", 1024**3),
            ("2T", 2 * 1024**4),
            ("100KB", 100 * 1024),
            ("1.5GB", int(1.5 * 1024**3)),
            ("10B", 10),
        ],
    )
    def test_units(self, text, expected):
        assert _parse_size(text) == expected

    def test_case_insensitive_and_whitespace(self):
        assert _parse_size("  50mb ") == 50 * 1024**2
        assert _parse_size("1g") == 1024**3

    def test_bare_integer_is_bytes(self):
        assert _parse_size("12345") == 12345

    def test_unparseable_raises_typer_exit(self):
        with pytest.raises(typer.Exit) as exc_info:
            _parse_size("not-a-size")
        assert exc_info.value.exit_code == 1

    def test_unit_without_number_raises(self):
        with pytest.raises(typer.Exit):
            _parse_size("MB")


# -- gc._parse_docker_size (decimal, 1000-based) --------------------------

class TestDockerParseSize:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("3.2GB (58%)", 3200000000),
            ("1.5kB", 1500),
            ("0B", 0),
            ("1.2 TB", 1200000000000),
            ("456.7MB", 456700000),
            ("1KB", 1000),
        ],
    )
    def test_decimal_units(self, text, expected):
        assert _parse_docker_size(text) == expected

    def test_percentage_suffix_stripped(self):
        assert _parse_docker_size("1GB (100%)") == 1000**3

    def test_garbage_returns_zero(self):
        assert _parse_docker_size("garbage") == 0
        assert _parse_docker_size("xB") == 0
        assert _parse_docker_size("") == 0


# -- sentinel ollama size parsing (decimal HumanBytes -> GB) --------------

# Mirror of the line-match in sentinel._ollama_status: "NAME  ID  SIZE ..."
_LINE_RE = r"^(\S+)\s+\S+\s+" + _SIZE_RE


def _parse_ollama_line(line: str):
    m = re.match(_LINE_RE, line)
    if not m:
        return None
    return m.group(1), float(m.group(2)) * _SIZE_FACTOR_GB[m.group(3).upper()]


class TestSentinelOllamaSizes:
    def test_typical_gb_line(self):
        parsed = _parse_ollama_line("qwen2.5:7b  abcdef123456  4.8 GB  100% GPU")
        assert parsed is not None
        name, gb = parsed
        assert name == "qwen2.5:7b"
        assert gb == pytest.approx(4.8)

    @pytest.mark.parametrize(
        "size_text,expected_gb",
        [
            ("512 MB", 0.512),
            ("900 KB", 0.0000009 * 1000),  # 900 KB -> 9e-4 GB
            ("1.5 TB", 1500.0),
            ("123 B", 123e-9),
            ("2.1GB", 2.1),  # no space between number and unit
        ],
    )
    def test_all_units_handled(self, size_text, expected_gb):
        line = f"model:latest  id123  {size_text}  42% GPU"
        parsed = _parse_ollama_line(line)
        assert parsed is not None, f"unit not handled: {size_text}"
        assert parsed[1] == pytest.approx(expected_gb)

    def test_factor_table_covers_regex_units(self):
        # Every unit the regex can capture must have a conversion factor:
        # a missing one would raise KeyError inside _ollama_status and
        # silently drop models from the unload list.
        for unit in ("B", "KB", "MB", "GB", "TB"):
            assert unit in _SIZE_RE
            assert unit in _SIZE_FACTOR_GB
        assert _SIZE_FACTOR_GB["GB"] == 1.0
        assert _SIZE_FACTOR_GB["TB"] == 1e3
        assert _SIZE_FACTOR_GB["MB"] == 1e-3

    def test_header_and_malformed_lines_do_not_match(self):
        assert _parse_ollama_line("NAME  ID  SIZE  PROCESSOR  UNTIL") is None
        assert _parse_ollama_line("") is None
