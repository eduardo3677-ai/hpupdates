"""Tests for OS code generation — mirrors HP.SUDFClient.ScanParams.OSParamsCreator."""

from __future__ import annotations

import pytest

from hpupdates.infrastructure.os_params import (
    OSParams,
    OSProfix,
    _get_same_word_count,
    _load_osparams,
    create_os_code,
    create_os_codes,
    is_win11,
    os_version_name,
    report_os_version,
)

# ---------------------------------------------------------------------------
# is_win11
# ---------------------------------------------------------------------------


class TestIsWin11:
    def test_major_greater_than_10(self) -> None:
        assert is_win11(11, 0, 0) is True
        assert is_win11(12, 0, 0) is True

    def test_major_10_build_at_threshold(self) -> None:
        assert is_win11(10, 0, 22000) is True

    def test_major_10_build_below_threshold(self) -> None:
        assert is_win11(10, 0, 21999) is False

    def test_major_10_build_far_above_threshold(self) -> None:
        assert is_win11(10, 0, 26100) is True

    def test_major_below_10(self) -> None:
        assert is_win11(6, 1, 7601) is False
        assert is_win11(9, 0, 0) is False


# ---------------------------------------------------------------------------
# os_version_name
# ---------------------------------------------------------------------------


class TestOsVersionName:
    @pytest.mark.parametrize(
        "major,minor,build,expected",
        [
            (5, 1, 2600, "Windows XP"),
            (6, 0, 6000, "Windows Vista"),
            (6, 1, 7601, "Windows 7"),
            (6, 2, 9200, "Windows 8"),
            (6, 3, 9600, "Windows 8.1"),
            (10, 0, 19045, "Windows 10"),
            (10, 0, 22000, "Windows 11"),
            (10, 0, 26100, "Windows 11"),
            (11, 0, 0, "Windows 11"),
            (12, 0, 0, "Windows 11"),
        ],
    )
    def test_version_name_mapping(self, major: int, minor: int, build: int, expected: str) -> None:
        assert os_version_name(major, minor, build) == expected

    def test_unknown_minor_falls_back_to_8_1(self) -> None:
        """Major 6 with an unmapped minor falls back to Windows 8.1."""
        assert os_version_name(6, 9, 0) == "Windows 8.1"

    def test_major_below_6_is_xp(self) -> None:
        assert os_version_name(0, 0, 0) == "Windows XP"
        assert os_version_name(5, 9, 9999) == "Windows XP"


# ---------------------------------------------------------------------------
# report_os_version
# ---------------------------------------------------------------------------


class TestReportOsVersion:
    def test_win11_uses_num_8(self) -> None:
        """Win11 (build >= 22000) should produce num=8 in the compact string."""
        result = report_os_version("8A4F", "64", "10.0.26100", "24H2")
        assert result == "8.8A4F.64.24H2"

    def test_win10_uses_major_minus_5(self) -> None:
        result = report_os_version("8A4F", "64", "10.0.19045", "22H2")
        assert result == "5.8A4F.64.22H2"

    def test_win7_uses_major_minus_5(self) -> None:
        result = report_os_version("8A4F", "64", "6.1.7601", "")
        assert result == "1.8A4F.64."

    def test_xp_uses_major_minus_5(self) -> None:
        result = report_os_version("8A4F", "32", "5.1.2600", "")
        assert result == "0.8A4F.32."

    def test_architecture_truncated_to_two_chars(self) -> None:
        result = report_os_version("SKU", "64-bit", "10.0.26100", "24H2")
        assert result.startswith("8.SKU.64.")

    def test_short_architecture_preserved(self) -> None:
        result = report_os_version("SKU", "32", "10.0.19045", "22H2")
        assert result == "5.SKU.32.22H2"


# ---------------------------------------------------------------------------
# _get_same_word_count
# ---------------------------------------------------------------------------


class TestGetSameWordCount:
    def test_exact_match(self) -> None:
        assert _get_same_word_count("Windows Pro", "Windows Pro") == 2

    def test_case_insensitive(self) -> None:
        assert _get_same_word_count("windows pro", "Windows Pro") == 2

    def test_partial_overlap(self) -> None:
        assert _get_same_word_count("Windows Pro", "Windows Home") == 1

    def test_no_overlap(self) -> None:
        assert _get_same_word_count("abc", "xyz") == 0

    def test_empty_pattern(self) -> None:
        assert _get_same_word_count("", "anything") == 0

    def test_repeated_words_counted_multiple_times(self) -> None:
        """If 'Pro' appears twice in pattern and once in content, count is 2."""
        assert _get_same_word_count("Pro Pro", "Pro") == 2


# ---------------------------------------------------------------------------
# _load_osparams
# ---------------------------------------------------------------------------


class TestLoadOsParams:
    def test_returns_proper_structure(self) -> None:
        params = _load_osparams()
        assert isinstance(params, OSParams)
        assert len(params.profixs) >= 5
        assert len(params.product_names) >= 11
        assert len(params.rules) >= 8

    def test_profixs_contain_known_entries(self) -> None:
        params = _load_osparams()
        profix_names = {p.name for p in params.profixs}
        assert {"W7", "W8", "W8.1", "WT", "W11"}.issubset(profix_names)

    def test_rules_contain_expected_mappings(self) -> None:
        params = _load_osparams()
        rule_names = {r.name for r in params.rules}
        assert {"W7", "W8", "W8.1", "WT", "W11"}.issubset(rule_names)

    def test_dataclasses_are_frozen(self) -> None:
        profix = OSProfix(name="W7", value="Windows 7")
        with pytest.raises(AttributeError):
            profix.name = "W8"  # type: ignore[misc]

    def test_rule_mapping_is_tuple(self) -> None:
        params = _load_osparams()
        for rule in params.rules:
            assert isinstance(rule.mapping, tuple)


# ---------------------------------------------------------------------------
# create_os_codes
# ---------------------------------------------------------------------------


class TestCreateOsCodes:
    def test_windows_7_professional_64(self) -> None:
        codes = create_os_codes("Windows 7 Professional", "Windows 7", "64", "")
        assert codes == ["W764PR"]

    def test_windows_7_professional_32(self) -> None:
        codes = create_os_codes("Windows 7 Professional", "Windows 7", "32", "")
        assert codes == ["W732PR"]

    def test_windows_8_pro_64(self) -> None:
        codes = create_os_codes("Windows 8 Pro", "Windows 8", "64", "")
        assert codes == ["W864"]

    def test_windows_8_1_pro_64(self) -> None:
        codes = create_os_codes("Windows 8.1 Pro", "Windows 8.1", "64", "")
        assert codes == ["W8.164"]

    def test_windows_10_pro_64_22h2(self) -> None:
        codes = create_os_codes("Windows 10 Pro", "Windows 10", "64", "22H2")
        assert codes == ["WT64_22H2", "WT64"]

    def test_windows_10_pro_32_22h2(self) -> None:
        codes = create_os_codes("Windows 10 Pro", "Windows 10", "32", "22H2")
        assert codes == ["WT32_22H2", "WT32"]

    def test_windows_10_pro_64_release_id_2009(self) -> None:
        codes = create_os_codes("Windows 10 Pro", "Windows 10", "64", "2009")
        assert codes == ["WT64_2009", "WT64"]

    def test_windows_11_pro_64_23h2(self) -> None:
        codes = create_os_codes("Windows 11 Pro", "Windows 11", "64", "23H2")
        assert codes == ["W11_23H2"]

    def test_windows_11_pro_64_24h2(self) -> None:
        codes = create_os_codes("Windows 11 Pro", "Windows 11", "64", "24H2")
        assert codes == ["W11_24H2"]

    def test_windows_11_home_64_23h2(self) -> None:
        codes = create_os_codes("Windows 11 Home", "Windows 11", "64", "23H2")
        assert codes == ["W11_23H2"]

    def test_windows_11_enterprise_64_23h2(self) -> None:
        codes = create_os_codes("Windows 11 Enterprise", "Windows 11", "64", "23H2")
        assert codes == ["W11_23H2"]

    def test_windows_10_home_64_22h2(self) -> None:
        codes = create_os_codes("Windows 10 Home", "Windows 10", "64", "22H2")
        assert codes == ["WT64_22H2", "WT64"]

    def test_first_code_is_primary(self) -> None:
        """create_os_code returns the first element of create_os_codes."""
        params = ("Windows 10 Pro", "Windows 10", "64", "22H2")
        assert create_os_code(*params) == create_os_codes(*params)[0]

    def test_win10_empty_release_id(self) -> None:
        """Win10 with empty release_id: the version rule is skipped (empty token)."""
        codes = create_os_codes("Windows 10 Pro", "Windows 10", "64", "")
        # First rule WT with OSVersion empty -> skipped; second rule WT64 -> present
        assert codes == ["WT64"]

    def test_win11_empty_release_id(self) -> None:
        """Win11 with empty release_id: the only W11 rule uses OSVersion -> skipped."""
        codes = create_os_codes("Windows 11 Pro", "Windows 11", "64", "")
        assert codes == []

    def test_no_matching_profix_returns_empty(self) -> None:
        codes = create_os_codes("Linux Ubuntu", "Linux", "64", "22H2")
        assert codes == []

    def test_case_insensitive_profix_match(self) -> None:
        codes = create_os_codes("windows 10 pro", "windows 10", "64", "22H2")
        assert codes[0] == "WT64_22H2"


# ---------------------------------------------------------------------------
# create_os_code (primary)
# ---------------------------------------------------------------------------


class TestCreateOsCode:
    def test_returns_first_code(self) -> None:
        assert create_os_code("Windows 10 Pro", "Windows 10", "64", "22H2") == "WT64_22H2"

    def test_returns_empty_string_when_no_codes(self) -> None:
        assert create_os_code("Linux Ubuntu", "Linux", "64", "22H2") == ""

    def test_win11_23h2(self) -> None:
        assert create_os_code("Windows 11 Pro", "Windows 11", "64", "23H2") == "W11_23H2"

    def test_win7_32_pr(self) -> None:
        assert create_os_code("Windows 7 Professional", "Windows 7", "32", "") == "W732PR"
