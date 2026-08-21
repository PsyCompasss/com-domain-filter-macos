import random
import unittest

from com_domain_filter.patterns import PatternConfigurationError, PatternGenerator


class PatternGeneratorTests(unittest.TestCase):
    def test_repetition_pattern_and_fixed_parts(self):
        generator = PatternGenerator(
            "abc123",
            ["AABBCC"],
            prefix="abc",
            suffix="88",
            rng=random.Random(7),
        )
        item = generator.generate()
        self.assertTrue(item.domain.startswith("abc"))
        self.assertTrue(item.domain.endswith("88.com"))
        random_part = item.random_part
        self.assertEqual(random_part[0], random_part[1])
        self.assertEqual(random_part[2], random_part[3])
        self.assertEqual(random_part[4], random_part[5])
        self.assertEqual(len({random_part[0], random_part[2], random_part[4]}), 3)

    def test_unlimited_uses_requested_length(self):
        generator = PatternGenerator("ab", ["不限"], unlimited_length=9, rng=random.Random(2))
        item = generator.generate()
        self.assertEqual(len(item.random_part), 9)
        self.assertEqual(len(item.domain), 13)

    def test_not_enough_distinct_characters_is_rejected(self):
        with self.assertRaises(PatternConfigurationError):
            PatternGenerator("ab", ["ABC"])

    def test_custom_text_is_normalized(self):
        generator = PatternGenerator("ab", ["AAA"], prefix=" ABC ", suffix="88")
        self.assertEqual(generator.prefix, "abc")

    def test_hyphen_is_allowed_inside_but_never_at_ends(self):
        generator = PatternGenerator("a-", ["不限"], unlimited_length=12, rng=random.Random(9))
        for _ in range(30):
            label = generator.generate().domain.removesuffix(".com")
            self.assertFalse(label.startswith("-"))
            self.assertFalse(label.endswith("-"))

    def test_fixed_outer_hyphen_is_rejected(self):
        with self.assertRaises(PatternConfigurationError):
            PatternGenerator("ab", ["AAA"], prefix="-abc")
        with self.assertRaises(PatternConfigurationError):
            PatternGenerator("ab", ["AAA"], suffix="88-")

    def test_space_estimate(self):
        generator = PatternGenerator("abc", ["AAA", "ABC", "不限"], unlimited_length=2)
        self.assertEqual(generator.estimated_space(), 3 + 6 + 9)


if __name__ == "__main__":
    unittest.main()
