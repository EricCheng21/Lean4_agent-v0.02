set_option autoImplicit false

namespace FormalAgent

theorem identity (P : Prop) : P → P := by
  intro h0
  exact h0

theorem andSwap (P Q : Prop) : P ∧ Q → Q ∧ P := by
  intro h0
  constructor
  exact h0.2
  exact h0.1

end FormalAgent

