import Lean
import FormalAgent.Protocol

open Lean Meta Elab Tactic

namespace FormalAgent

private def localDeclView (decl : LocalDecl) : MetaM LocalDeclView := do
  let type ← instantiateMVars decl.type
  return {
    userName := decl.userName.toString
    typePretty := (← ppExpr type).pretty
    typeRepr := reprStr type
    isLet := decl.isLet
  }

private def goalView (goal : MVarId) : MetaM GoalView := goal.withContext do
  let target ← instantiateMVars (← goal.getType)
  let mut localContext := #[]
  for decl in ← getLCtx do
    if !decl.isImplementationDetail then
      localContext := localContext.push (← localDeclView decl)
  return {
    targetPretty := (← ppExpr target).pretty
    targetRepr := reprStr target
    localContext
  }

private def emitGoalViews (views : Array GoalView) : TacticM Unit := do
  for view in views do
    let envelope : GoalEnvelope := { schemaVersion := 1, goal := view }
    logInfo m!"FORMAL_AGENT_GOAL:{(toJson envelope).compress}"

private def currentGoalViews : TacticM (Array GoalView) := do
  let mut views := #[]
  for goal in ← getUnsolvedGoals do
    views := views.push (← goalView goal)
  return views

/-- Emit one machine-readable line for the focused goal without changing it. -/
meta def dumpGoal : TacticM Unit := withMainContext do
  let view ← goalView (← getMainGoal)
  let envelope : GoalEnvelope := { schemaVersion := 1, goal := view }
  logInfo m!"FORMAL_AGENT_GOAL:{(toJson envelope).compress}"

private structure StoredProofState where
  saved : Tactic.SavedState
  coreContext : Core.Context
  metaContext : Meta.Context
  termContext : Term.Context
  tacticContext : Tactic.Context

private builtin_initialize proofStateStore : IO.Ref (Std.HashMap String StoredProofState) ←
  IO.mkRef {}

private def captureProofState : TacticM StoredProofState := do
  return {
    saved := ← Tactic.saveState
    coreContext := ← readThe Core.Context
    metaContext := ← readThe Meta.Context
    termContext := ← readThe Term.Context
    tacticContext := ← readThe Tactic.Context
  }

private def saveProofState (stateId : String) : TacticM Unit := do
  let states ← proofStateStore.get
  if states.contains stateId then
    throwError "proof state already exists: {stateId}"
  let state ← captureProofState
  proofStateStore.modify fun states => states.insert stateId state

private def getProofState (stateId : String) : TacticM StoredProofState := do
  let some state := (← proofStateStore.get)[stateId]?
    | throwError "unknown proof state: {stateId}"
  return state

/--
`Tactic.SavedState.restore` intentionally restores only backtrackable fields.
Handles cross JSONL requests, whose fresh command states reuse name-generator
counters, so they require a full state restore to avoid metavariable ID
collisions. `withProofStateContext` separately reinstates the immutable reader
contexts while the stored branch is active.
-/
private def restoreProofStateFull (state : StoredProofState) : TacticM Unit := do
  modifyThe Core.State fun _ => state.saved.term.meta.core.toState
  modifyThe Meta.State fun _ => state.saved.term.meta.meta
  modifyThe Term.State fun _ => state.saved.term.elab
  modifyThe Tactic.State fun _ => state.saved.tactic

private def withProofStateContext (state : StoredProofState)
    (action : TacticM α) : TacticM α :=
  withTheReader Core.Context (fun _ => state.coreContext) <|
    withTheReader Meta.Context (fun _ => state.metaContext) <|
      withTheReader Term.Context (fun _ => state.termContext) <|
        withTheReader Tactic.Context (fun _ => state.tacticContext) action

/-- Parser node for the machine-readable goal probe. -/
syntax (name := agentDumpGoal) "agent_dump_goal" : tactic

/--
The driver links this elaborator as a builtin so it can be called repeatedly
without interpreting an imported tactic declaration on every request.
-/
@[builtin_tactic agentDumpGoal]
def evalAgentDumpGoal : Tactic := fun _ => dumpGoal

/-- Save the current tactic state under an opaque process-local handle. -/
syntax (name := agentSaveState) "agent_save_state " str : tactic

@[builtin_tactic agentSaveState]
def evalAgentSaveState : Tactic
  | `(tactic| agent_save_state $stateId:str) => do
    saveProofState stateId.getString
    emitGoalViews (← currentGoalViews)
  | _ => throwUnsupportedSyntax

/--
Restore `parentStateId`, apply exactly one tactic, and save the resulting state
as `childStateId`. The surrounding dummy theorem state is restored afterwards,
so failed branches cannot mutate either the parent or the request wrapper.
-/
syntax (name := agentApplyState)
  "agent_apply_state" str str "=>" tactic : tactic

@[builtin_tactic agentApplyState]
def evalAgentApplyState : Tactic
  | `(tactic| agent_apply_state $parentStateId:str $childStateId:str => $action:tactic) => do
    let parent ← getProofState parentStateId.getString
    let dummy ← captureProofState
    let result : Except Exception (Array GoalView) ← try
      withProofStateContext parent do
        restoreProofStateFull parent
        Term.withoutErrToSorry <| Tactic.withoutRecover <| evalTactic action
        pruneSolvedGoals
        saveProofState childStateId.getString
        pure (Except.ok (← currentGoalViews))
    catch error =>
      pure (Except.error error)
    restoreProofStateFull dummy
    match result with
    | .ok views => emitGoalViews views
    | .error error => throw error
  | _ => throwUnsupportedSyntax

/-- Release one process-local state handle. -/
syntax (name := agentReleaseState) "agent_release_state " str : tactic

@[builtin_tactic agentReleaseState]
def evalAgentReleaseState : Tactic
  | `(tactic| agent_release_state $stateId:str) =>
    proofStateStore.modify fun states => states.erase stateId.getString
  | _ => throwUnsupportedSyntax

end FormalAgent
