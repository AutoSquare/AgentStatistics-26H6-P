# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from antigravity_usage_fields import build_antigravity_usage


class AntigravityUsageFieldsTests(unittest.TestCase):
    def test_input_tokens_include_cache_like_cursor(self) -> None:
        usage = build_antigravity_usage(56000, 3000, cache_read=380000, cache_write=0, reasoning=0)
        self.assertEqual(usage["input_tokens"], 436000)
        self.assertEqual(usage["cached_input_tokens"], 380000)
        self.assertEqual(usage["total_tokens"], 439000)

    def test_reasoning_is_not_double_counted_in_total(self) -> None:
        usage = build_antigravity_usage(12, 4, cache_read=2, cache_write=0, reasoning=1)
        self.assertEqual(usage["input_tokens"], 14)
        self.assertEqual(usage["output_tokens"], 3)
        self.assertEqual(usage["total_tokens"], 18)


if __name__ == "__main__":
    unittest.main()
