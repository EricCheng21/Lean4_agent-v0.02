import unittest

from formal_agent.formula import Formula


class FormulaTest(unittest.TestCase):
    def test_lean_rendering_preserves_precedence(self):
        p = Formula.atom("P")
        q = Formula.atom("Q")
        r = Formula.atom("R")
        formula = Formula.implication(Formula.implication(p, q), r)
        self.assertEqual(formula.to_lean(), "(P → Q) → R")

    def test_round_trip_dict(self):
        formula = Formula.conjunction(Formula.atom("P"), Formula.atom("Q"))
        self.assertEqual(Formula.from_dict(formula.to_dict()), formula)


if __name__ == "__main__":
    unittest.main()

