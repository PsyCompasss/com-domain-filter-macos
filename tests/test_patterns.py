import random
import unittest

from com_domain_filter.patterns import (
    BIND_INDEPENDENT,
    BIND_SHARED,
    BLOCK_COMMON,
    BLOCK_CUSTOM,
    BLOCK_FIXED,
    BLOCK_UNLIMITED,
    BlockPatternGenerator,
    PatternBlock,
    PatternConfigurationError,
    PatternGenerator,
)


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

    def test_free_form_blocks_generate_requested_shape(self):
        generator = BlockPatternGenerator(
            "abcdefghijk12345",
            (
                PatternBlock(BLOCK_CUSTOM, "ABCDDDD"),
                PatternBlock(BLOCK_FIXED, "12321"),
            ),
            rng=random.Random(5),
        )
        label = generator.generate().domain[:-4]
        self.assertEqual(len(label), 12)
        self.assertEqual(label[-5:], "12321")
        self.assertEqual(len(set(label[3:7])), 1)

    def test_shared_binding_reuses_placeholders_across_blocks(self):
        generator = BlockPatternGenerator(
            "abcdef",
            (
                PatternBlock(BLOCK_COMMON, "AAA"),
                PatternBlock(BLOCK_CUSTOM, "ABCD"),
                PatternBlock(BLOCK_COMMON, "AABB"),
            ),
            BIND_SHARED,
            rng=random.Random(2),
        )
        label = generator.generate().domain[:-4]
        self.assertEqual(label[0], label[3])
        self.assertEqual(label[3], label[7])
        self.assertEqual(label[4], label[9])

    def test_independent_binding_redraws_each_pattern_block(self):
        generator = BlockPatternGenerator(
            "abcdefghij",
            (PatternBlock(BLOCK_COMMON, "AAA"), PatternBlock(BLOCK_COMMON, "AAA")),
            BIND_INDEPENDENT,
            rng=random.Random(4),
        )
        label = generator.generate().domain[:-4]
        self.assertNotEqual(label[0], label[3])

    def test_unlimited_block_keeps_its_own_length(self):
        generator = BlockPatternGenerator(
            "abc123",
            (PatternBlock(BLOCK_FIXED, "x"), PatternBlock(BLOCK_UNLIMITED, length=7)),
            rng=random.Random(3),
        )
        self.assertEqual(len(generator.generate().domain[:-4]), 8)


if __name__ == "__main__":
    unittest.main()
