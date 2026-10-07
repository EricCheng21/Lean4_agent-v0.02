import shutil
import unittest

from formal_agent.formula import Formula
from formal_agent.lean_env import LeanVerifier


@unittest.skipUnless(shutil.which("lean"), "Lean is not installed")
class LeanVerifierTest(unittest.TestCase):
    def test_replays_valid_proof(self):
        p = Formula.atom("P")
        result = LeanVerifier().verify(
            Formula.implication(p, p), ["intro h0", "exact h0"]
        )
        self.assertTrue(result.verified, result.stderr)

    def test_rejects_incomplete_proof(self):
        p = Formula.atom("P")
        result = LeanVerifier().verify(Formula.implication(p, p), ["intro h0"])
        self.assertFalse(result.verified)


if __name__ == "__main__":
    unittest.main()

