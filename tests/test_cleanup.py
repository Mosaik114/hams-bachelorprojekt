"""Tests for cleanup text processing."""

import pytest
from unittest.mock import patch, MagicMock
from wisper.main import FloatingTranscriberApp, CFG


class TestCleanupText:
    """Tests for text cleanup functionality."""

    def test_cleanup_prompt_format(self):
        """Cleanup prompt should contain {text} placeholder."""
        template = CFG.cleanup_prompt_template
        assert "{text}" in template or "Text:" in template

    def test_cleanup_prompt_not_empty(self):
        """Cleanup prompt should not be empty."""
        template = CFG.cleanup_prompt_template
        assert len(template) > 0

    def test_cleanup_prompt_contains_instructions(self):
        """Cleanup prompt should contain key instructions."""
        template = CFG.cleanup_prompt_template
        assert "korrigiere" in template.lower() or "bereinige" in template.lower()

