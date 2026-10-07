import unittest

from formal_agent.environment import PropositionalEnvironment
from formal_agent.formula import Formula
from formal_agent.search import SearchBudget, best_first_search


class SearchTest(unittest.TestCase):
    def test_solves_and_swap_and_records_invalid_attempts(self):
        p = Formula.atom("P")
        q = Formula.atom("Q")
        target = Formula.implication(
            Formula.conjunction(p, q), Formula.conjunction(q, p)
        )
        environment = PropositionalEnvironment()
        result = best_first_search(
            environment,
            environment.initial_state(target),
            SearchBudget(),
        )
        self.assertTrue(result.solved)
        self.assertEqual(
            result.tactics,
            ["intro h0", "constructor", "exact h0.2", "exact h0.1"],
        )
        self.assertGreater(result.metrics.invalid_transitions, 0)

    def test_budget_stops_search(self):
        p = Formula.atom("P")
        environment = PropositionalEnvironment()
        result = best_first_search(
            environment,
            environment.initial_state(p),
            SearchBudget(max_expansions=0),
        )
        self.assertEqual(result.status, "budget_exceeded")

    def test_duplicate_transitions_remain_in_experience_graph(self):
        p = Formula.atom("P")
        environment = PropositionalEnvironment()
        target = Formula.implication(Formula.conjunction(p, p), p)
        result = best_first_search(
            environment,
            environment.initial_state(target),
            SearchBudget(),
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.metrics.duplicate_transitions, 1)
        solved_targets = [
            attempt
            for attempt in result.graph.attempts
            if attempt.valid and attempt.target_id == result.final_state_id
        ]
        self.assertEqual(len(solved_targets), 2)


if __name__ == "__main__":
    unittest.main()
