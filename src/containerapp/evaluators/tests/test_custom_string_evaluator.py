import unittest

from src.evaluators.custom_string_evaluator import CustomStringEvaluator


class TestCustomStringEvaluator(unittest.TestCase):
    def test_string_evaluator_exact_match(self):
        evaluator = CustomStringEvaluator()
        exact_match = evaluator("value", "value")
        no_match = evaluator("value", "not_value")
        assert exact_match
        assert not no_match

    def test_string_evaluator_commas_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("value", "va,lue", config={CustomStringEvaluator.Config.IGNORE_COMMAS: True})
        assert match_1

    def test_string_evaluator_commas_not_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("value", "value", config={CustomStringEvaluator.Config.IGNORE_COMMAS: False})
        match_2 = evaluator("value", "va,lue", config={CustomStringEvaluator.Config.IGNORE_COMMAS: False})
        assert match_1
        assert not match_2

    def test_string_evaluator_dots_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("value", "va.lue", config={CustomStringEvaluator.Config.IGNORE_DOTS: True})
        assert match_1

    def test_string_evaluator_dots_not_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("value", "value", config={CustomStringEvaluator.Config.IGNORE_DOTS: False})
        match_2 = evaluator("value", "va.lue", config={CustomStringEvaluator.Config.IGNORE_DOTS: False})
        assert match_1
        assert not match_2

    def test_string_evaluator_dollar_sign_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("$10", "10", config={CustomStringEvaluator.Config.IGNORE_DOLLAR_SIGN: True})
        assert match_1

    def test_string_evaluator_dollar_sign_not_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("$10", "10", config={CustomStringEvaluator.Config.IGNORE_DOLLAR_SIGN: False})
        assert not match_1

    def test_string_evaluator_parenthesis_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator(
            "(256)3300488", "2563300488", config={CustomStringEvaluator.Config.IGNORE_PARENTHETHES: True}
        )
        assert match_1

    def test_string_evaluator_parenthesis_not_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator(
            "(256)3300488", "2563300488", config={CustomStringEvaluator.Config.IGNORE_PARENTHETHES: False}
        )
        assert not match_1

    def test_string_evaluator_dashes_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("(256)330-0488", "(256)3300488", config={CustomStringEvaluator.Config.IGNORE_DASHES: True})
        assert match_1

    def test_string_evaluator_dashes_not_ignored(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator("(256)3300-488", "(256)3300488", config={CustomStringEvaluator.Config.IGNORE_DASHES: False})
        assert not match_1

    def test_string_evaluator_additional_matches(self):
        evaluator = CustomStringEvaluator()
        match_1 = evaluator(
            "correct", "correct", config={CustomStringEvaluator.Config.ADDITIONAL_MATCHES: ["yes", "true"]}
        )
        match_2 = evaluator("correct", "yes", config={CustomStringEvaluator.Config.ADDITIONAL_MATCHES: ["yes", "true"]})
        match_3 = evaluator(
            "correct", "true", config={CustomStringEvaluator.Config.ADDITIONAL_MATCHES: ["yes", "true"]}
        )
        match_4 = evaluator(
            "correct", "false", config={CustomStringEvaluator.Config.ADDITIONAL_MATCHES: ["yes", "true"]}
        )
        assert match_1
        assert match_2
        assert match_3
        assert not match_4
