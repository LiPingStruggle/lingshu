#!/usr/bin/env python3
"""Tests for parser module"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.parser import parse_input, safe_parse, validate_encoding, ParserError


class TestParser:
    """Test the parser functions"""

    def test_parse_simple_text(self):
        """Test parsing simple text"""
        result = parse_input("hello world")
        assert result == ["hello", "world"]

    def test_parse_quoted_text(self):
        """Test parsing with quotes"""
        result = parse_input('say "hello world"')
        assert '"hello world"' in result

    def test_parse_empty_input(self):
        """Test parsing empty input"""
        result = parse_input("")
        assert result == []

    def test_parse_whitespace_only(self):
        """Test parsing whitespace only"""
        result = parse_input("   ")
        assert result == []

    def test_parse_special_characters(self):
        """Test parsing with special characters (BUG001 regression)"""
        result = parse_input("@#$%")
        # Should not crash, should return token list
        assert isinstance(result, list)

    def test_parse_mixed_quotes(self):
        """Test parsing mixed quote styles"""
        result = parse_input("a 'b c' d")
        assert "'b c'" in result

    def test_parse_chinese(self):
        """Test parsing Chinese text"""
        result = parse_input("你好 世界")
        assert "你好" in result
        assert "世界" in result

    def test_safe_parse_normal(self):
        """Test safe_parse with normal input"""
        result = safe_parse("hello")
        assert result == ["hello"]

    def test_safe_parse_error(self):
        """Test safe_parse raises ParserError on bad input"""
        # safe_parse wraps exceptions in ParserError
        # None is handled gracefully (returns []), so test with a value that raises
        class BadStr:
            def strip(self):
                raise ValueError("mock error")
        with pytest.raises(ParserError):
            safe_parse(BadStr())  # type: ignore

    def test_validate_encoding_utf8(self):
        """Test validate_encoding with UTF-8"""
        assert validate_encoding("hello") is True
        assert validate_encoding("你好") is True

    def test_validate_encoding_invalid(self):
        """Test validate_encoding with invalid data"""
        # bytes that are invalid UTF-8 should return False
        result = validate_encoding("\ud800")  # lone surrogate
        assert result is False

    def test_parse_unicode_normalization(self):
        """Test Unicode normalization"""
        result = parse_input("café")
        assert "café" in result

    def test_parse_error_recovery(self):
        """Test parser error recovery"""
        # After an error, parser should still be usable
        try:
            safe_parse(None)  # type: ignore - intentional error
        except ParserError:
            pass
        # Subsequent calls should still work
        result = parse_input("1 + 2")
        assert result is not None
        assert len(result) > 0
