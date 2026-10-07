import unittest

from formal_agent.action import Action
from formal_agent.environment import PropositionalEnvironment
from formal_agent.formula import Formula


class EnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.environment = PropositionalEnvironment()
        self.p = Formula.atom("P")
        self.q = Formula.atom("Q")

    def test_split_keeps_both_open_goals_in_one_state(self):
        state = self.environment.initial_state(Formula.conjunction(self.p, self.q))
        result = self.environment.apply(state, Action("split_and"))
        self.assertTrue(result.valid)
        self.assertEqual(len(result.state.goals), 2)

    def test_invalid_attempt_has_no_target_state(self):
        state = self.environment.initial_state(self.p)
        result = self.environment.apply(state, Action("intro"))
        self.assertFalse(result.valid)
        self.assertIsNone(result.state)
        self.assertEqual(result.error_code, "shape_mismatch")


if __name__ == "__main__":
    unittest.main()

