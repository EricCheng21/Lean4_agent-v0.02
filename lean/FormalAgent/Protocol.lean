import Lean
import Lean.Data.Json.FromToJson

open Lean

namespace FormalAgent

/-- Human-readable view of a local declaration at a probed proof state. -/
structure LocalDeclView where
  userName : String
  typePretty : String
  typeRepr : String
  isLet : Bool
deriving ToJson, FromJson

/-- One open Lean goal. Expression reprs are observations, not stable state IDs. -/
structure GoalView where
  targetPretty : String
  targetRepr : String
  localContext : Array LocalDeclView
deriving ToJson, FromJson

/-- Versioned wire envelope for one focused goal. -/
structure GoalEnvelope where
  schemaVersion : Nat
  goal : GoalView
deriving ToJson, FromJson

end FormalAgent
