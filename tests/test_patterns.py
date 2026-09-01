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
    ContainmentRule,
    ImportedDomainGenerator,
    PatternBlock,
    PatternConfigurationError,
    PatternGenerator,
    normalize_imported_domains,
    normalize_domain_suffixes,
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

    def test_at_least_contains_two_eights(self):
        generator = BlockPatternGenerator(
            "abc8",
            (PatternBlock(BLOCK_UNLIMITED, length=6),),
            containment_rules=(ContainmentRule("8", 2),),
            rng=random.Random(11),
        )
        for _ in range(30):
            self.assertGreaterEqual(generator.generate().domain[:-4].count("8"), 2)

    def test_at_least_contains_requires_character_in_pool(self):
        with self.assertRaises(PatternConfigurationError):
            BlockPatternGenerator(
                "abc",
                (PatternBlock(BLOCK_UNLIMITED, length=4),),
                containment_rules=(ContainmentRule("8", 2),),
            )

    def test_custom_pattern_can_move_as_one_piece(self):
        generator = BlockPatternGenerator(
            "abc123",
            (
                PatternBlock(BLOCK_UNLIMITED, length=2),
                PatternBlock(BLOCK_CUSTOM, "AA", random_position=True),
            ),
            rng=random.Random(19),
        )
        positions = set()
        for _ in range(80):
            label = generator.generate().domain[:-4]
            repeated = next(index for index in range(3) if label[index] == label[index + 1])
            positions.add(repeated)
        self.assertEqual(positions, {0, 1, 2})

    def test_fixed_and_common_blocks_can_move_as_one_piece(self):
        generator = BlockPatternGenerator(
            "abcdef123",
            (
                PatternBlock(BLOCK_UNLIMITED, length=2),
                PatternBlock(BLOCK_FIXED, "88", random_position=True),
                PatternBlock(BLOCK_COMMON, "AAA", random_position=True),
            ),
            rng=random.Random(23),
        )
        for _ in range(30):
            label = generator.generate().domain.split(".", 1)[0]
            self.assertIn("88", label)
            self.assertTrue(any(label[index] == label[index + 1] == label[index + 2] for index in range(len(label) - 2)))

    def test_selected_domain_suffixes_are_grouped_under_one_generated_stem(self):
        generator = BlockPatternGenerator(
            "ab",
            (PatternBlock(BLOCK_UNLIMITED, length=2),),
            domain_suffixes=(".com", ".net", ".cn", ".io"),
            rng=random.Random(7),
        )
        item = generator.generate()
        self.assertEqual(item.domain, f"{item.query_stem}.com")
        self.assertEqual(
            item.query_domains,
            tuple(f"{item.query_stem}{suffix}" for suffix in (".com", ".net", ".cn", ".io")),
        )
        # 组合数按主体计数，选择更多后缀不会重复生成同一个主体。
        self.assertEqual(generator.estimated_space(), 4)

    def test_readable_idn_suffix_is_accepted_and_old_punycode_setting_is_migrated(self):
        self.assertEqual(normalize_domain_suffixes((".中国",)), (".中国",))
        self.assertEqual(normalize_domain_suffixes((".xn--fiqs8s",)), (".中国",))

    def test_imported_idn_domains_are_saved_in_readable_form(self):
        self.assertEqual(
            normalize_imported_domains(("abc.中国", "abc.xn--fiqs8s"), (".中国",)),
            ("abc.中国",),
        )

    def test_imported_domains_are_normalized_deduplicated_and_sequential(self):
        domains = normalize_imported_domains(("Alpha", "beta.com", "https://alpha.com/path"))
        self.assertEqual(domains, ("alpha.com", "beta.com"))
        generator = ImportedDomainGenerator(domains)
        self.assertEqual(generator.generate().domain, "alpha.com")
        self.assertEqual(generator.generate().domain, "beta.com")
        with self.assertRaises(StopIteration):
            generator.generate()

    def test_import_rejects_non_com_suffix(self):
        with self.assertRaises(PatternConfigurationError):
            normalize_imported_domains(("example.net",))

    def test_import_expands_bare_names_across_selected_suffixes(self):
        domains = normalize_imported_domains(
            ("Alpha", "beta.net", "https://alpha.com/path"),
            (".com", ".net"),
        )
        self.assertEqual(domains, ("alpha.com", "alpha.net", "beta.net"))

    def test_imported_suffixes_are_grouped_by_stem_for_one_page_query(self):
        generator = ImportedDomainGenerator(
            ("alpha.com", "alpha.net", "alpha.cc", "beta.net"),
            (".com", ".net", ".cc"),
        )
        first = generator.generate()
        second = generator.generate()
        self.assertEqual(first.query_stem, "alpha")
        self.assertEqual(first.query_domains, ("alpha.com", "alpha.net", "alpha.cc"))
        self.assertEqual(second.query_stem, "beta")
        self.assertEqual(second.query_domains, ("beta.com", "beta.net", "beta.cc"))
        self.assertEqual(generator.estimated_space(), 2)

    def test_imported_com_list_uses_every_selected_suffix(self):
        generator = ImportedDomainGenerator(
            ("betel.com", "betas.com"),
            (".com", ".net", ".cc"),
        )
        first = generator.generate()
        second = generator.generate()
        self.assertEqual(first.query_domains, ("betel.com", "betel.net", "betel.cc"))
        self.assertEqual(second.query_domains, ("betas.com", "betas.net", "betas.cc"))


if __name__ == "__main__":
    unittest.main()
