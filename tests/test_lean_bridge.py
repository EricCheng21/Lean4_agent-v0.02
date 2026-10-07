import shutil
import unittest

from formal_agent.action import Action
from formal_agent.formula import Formula
from formal_agent.lean_bridge import LeanBridge
from formal_agent.lean_environment import LeanEnvironment
from formal_agent.search import SearchBudget, best_first_search


@unittest.skipUnless(shutil.which("lake") and shutil.which("lean"), "Lean/Lake unavailable")
class LeanBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = LeanBridge(timeout_seconds=20.0)
        cls.environment = LeanEnvironment(cls.bridge)

    def test_lean_owns_transition_validity(self):
        p = Formula.atom("P")
        state = self.environment.initial_state(Formula.implication(p, p))
        driver_pid = self.bridge.driver_process_id
        self.assertEqual(self.bridge.backend, "persistent_driver")
        self.assertIsNotNone(driver_pid)

        rejected = self.environment.apply(state, Action("split_and"))
        self.assertFalse(rejected.valid)
        self.assertIsNone(rejected.state)
        self.assertEqual(rejected.error_code, "shape_mismatch")
        self.assertEqual(self.bridge.driver_process_id, driver_pid)

        introduced = self.environment.apply(state, Action("intro"))
        self.assertTrue(introduced.valid)
        self.assertEqual(introduced.state.goals[0].target_pretty, "P")
        self.assertEqual(
            [decl.user_name for decl in introduced.state.goals[0].local_context],
            ["P", "h0"],
        )

        no_binder = self.environment.apply(introduced.state, Action("intro"))
        self.assertFalse(no_binder.valid)
        self.assertEqual(no_binder.error_code, "shape_mismatch")

        bad_projection = self.environment.apply(
            introduced.state, Action("and_left", hypothesis="h0")
        )
        self.assertFalse(bad_projection.valid)
        self.assertIsNone(bad_projection.state)

        solved = self.environment.apply(
            introduced.state, Action("exact", hypothesis="h0")
        )
        self.assertTrue(solved.valid)
        self.assertTrue(solved.state.solved)
        self.assertEqual(self.bridge.driver_process_id, driver_pid)

    def test_search_uses_lean_multi_goal_states(self):
        p = Formula.atom("P")
        q = Formula.atom("Q")
        target = Formula.implication(
            Formula.conjunction(p, q), Formula.conjunction(q, p)
        )
        initial = self.environment.initial_state(target)
        result = best_first_search(
            self.environment,
            initial,
            SearchBudget(timeout_seconds=60.0),
        )
        self.assertTrue(result.solved, result.message)
        self.assertEqual(
            result.tactics,
            ["intro h0", "constructor", "exact h0.2", "exact h0.1"],
        )
        two_goal_states = [
            node
            for node in result.graph.nodes.values()
            if len(node.state["goals_observed"]) == 2
        ]
        self.assertTrue(two_goal_states)
        released = self.bridge.apply_state(initial.driver_state_id, "intro h0")
        self.assertFalse(released.valid)
        self.assertEqual(released.error_code, "state_not_found")

    def test_driver_handles_preserve_context_and_can_be_released(self):
        p = Formula.atom("P")
        root = self.bridge.create_state(Formula.implication(p, p))
        self.assertTrue(root.valid, root.message)

        introduced = self.bridge.apply_state(root.state_id, "intro h0")
        self.assertTrue(introduced.valid, introduced.message)
        with_local_lemma = self.bridge.apply_state(
            introduced.state_id, "have hx : P := h0"
        )
        self.assertTrue(with_local_lemma.valid, with_local_lemma.message)
        self.assertEqual(
            [decl.user_name for decl in with_local_lemma.goals[0].local_context],
            ["P", "h0", "hx"],
        )

        sibling = self.bridge.apply_state(introduced.state_id, "exact h0")
        self.assertTrue(sibling.solved, sibling.message)

        released = self.bridge.release_state(with_local_lemma.state_id)
        self.assertTrue(released.valid, released.message)
        missing = self.bridge.apply_state(with_local_lemma.state_id, "exact hx")
        self.assertFalse(missing.valid)
        self.assertEqual(missing.error_code, "state_not_found")

    def test_lost_process_state_falls_back_to_trace_replay(self):
        p = Formula.atom("P")
        state = self.environment.initial_state(Formula.implication(p, p))
        self.assertIsNotNone(state.driver_state_id)

        self.bridge.close()
        introduced = self.environment.apply(state, Action("intro"))

        self.assertTrue(introduced.valid, introduced.message)
        self.assertEqual(introduced.state.goals[0].target_pretty, "P")
        self.assertIsNone(introduced.state.driver_state_id)
        self.assertIsNotNone(self.bridge.driver_process_id)

    def test_one_shot_replay_backend_remains_available(self):
        bridge = LeanBridge(timeout_seconds=20.0, persistent_driver=False)
        try:
            p = Formula.atom("P")
            result = bridge.probe(Formula.implication(p, p), ["intro h0"])
            self.assertTrue(result.valid, result.message)
            self.assertEqual(bridge.backend, "one_shot")
            self.assertEqual(result.goals[0].target_pretty, "P")
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
