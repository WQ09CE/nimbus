"""Tests for SimpleMemory."""

import unittest
from nimbus.core.memory import SimpleMemory


class TestSimpleMemory(unittest.TestCase):
    """Test cases for SimpleMemory class."""

    def setUp(self):
        """Set up test fixtures."""
        self.memory = SimpleMemory(max_turns=5)

    def test_add_turn(self):
        """Test adding conversation turns."""
        self.memory.add_turn("user", "Hello")
        self.memory.add_turn("assistant", "Hi there!")

        self.assertEqual(self.memory.get_turn_count(), 2)
        self.assertEqual(self.memory.history[0]["role"], "user")
        self.assertEqual(self.memory.history[1]["content"], "Hi there!")

    def test_max_turns_limit(self):
        """Test that history respects max_turns limit."""
        for i in range(10):
            self.memory.add_turn("user", f"Message {i}")

        self.assertEqual(self.memory.get_turn_count(), 5)
        # Should keep the most recent 5
        self.assertEqual(self.memory.history[0]["content"], "Message 5")
        self.assertEqual(self.memory.history[-1]["content"], "Message 9")

    def test_pin_and_unpin(self):
        """Test pinning and unpinning file metadata."""
        self.memory.pin("data.csv", "[csv] Sales data with 1000 rows")
        self.memory.pin("report.pdf", "[pdf] Q4 Financial Report")

        self.assertEqual(self.memory.get_pinned_count(), 2)
        self.assertIn("data.csv", self.memory.pinned)

        # Unpin
        removed = self.memory.unpin("data.csv")
        self.assertEqual(removed, "[csv] Sales data with 1000 rows")
        self.assertEqual(self.memory.get_pinned_count(), 1)

        # Unpin non-existent
        result = self.memory.unpin("nonexistent.txt")
        self.assertIsNone(result)

    def test_get_context_with_pinned(self):
        """Test context assembly with pinned items."""
        self.memory.pin("data.csv", "[csv] Sales data")
        self.memory.add_turn("user", "Analyze the data")
        self.memory.add_turn("assistant", "Sure, let me look at it.")

        context = self.memory.get_context()

        self.assertIn("## Uploaded Files", context)
        self.assertIn("data.csv", context)
        self.assertIn("## Recent Conversation", context)
        self.assertIn("Analyze the data", context)

    def test_get_context_recent_limit(self):
        """Test that get_context respects recent_count."""
        for i in range(10):
            self.memory.add_turn("user", f"Message {i}")

        context = self.memory.get_context(recent_count=3)

        # Should only include last 3 turns from memory (which has max 5)
        self.assertNotIn("Message 5", context)
        self.assertNotIn("Message 6", context)
        self.assertIn("Message 7", context)
        self.assertIn("Message 9", context)

    def test_clear(self):
        """Test clearing all memory."""
        self.memory.add_turn("user", "Hello")
        self.memory.pin("file.txt", "Some file")

        self.memory.clear()

        self.assertEqual(self.memory.get_turn_count(), 0)
        self.assertEqual(self.memory.get_pinned_count(), 0)

    def test_clear_history_keeps_pinned(self):
        """Test that clear_history keeps pinned items."""
        self.memory.add_turn("user", "Hello")
        self.memory.pin("file.txt", "Some file")

        self.memory.clear_history()

        self.assertEqual(self.memory.get_turn_count(), 0)
        self.assertEqual(self.memory.get_pinned_count(), 1)

    def test_context_truncates_long_content(self):
        """Test that long content is truncated in context."""
        long_message = "A" * 1000
        self.memory.add_turn("user", long_message)

        context = self.memory.get_context()

        # Should be truncated with "..."
        self.assertIn("...", context)
        self.assertLess(len(context), 1000)


if __name__ == "__main__":
    unittest.main()
