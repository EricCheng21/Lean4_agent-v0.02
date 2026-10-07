import Lean
import Lean.Data.Json.FromToJson
import FormalAgent.Probe

open Lean

namespace FormalAgent.Driver

private def schemaVersion : Nat := 1
private def goalMarker := "FORMAL_AGENT_GOAL:"

/-- One JSONL command. `source` is required except for `ping`. -/
structure Request where
  schemaVersion : Nat
  requestId : String
  method : String
  source : String
deriving FromJson

structure Diagnostic where
  severity : MessageSeverity
  message : String
deriving ToJson

structure Response where
  schemaVersion : Nat := schemaVersion
  requestId : String
  ok : Bool
  goals : Array GoalView := #[]
  diagnostics : Array Diagnostic := #[]
  errorCode : Option String := none
  message : String
  output : String := ""
deriving ToJson

private def failure (requestId errorCode message : String) : Response := {
  requestId
  ok := false
  errorCode := some errorCode
  message
}

private def parseGoalLine? (line : String) : Except String (Option GoalView) := do
  let payload? := match line.splitOn goalMarker with
    | [_] => none
    | _prefix :: payload :: _ => some payload
    | [] => none
  let some payload := payload? | return none
  let json ← Json.parse payload.trimAscii.copy
  let envelope : GoalEnvelope ← fromJson? json
  if envelope.schemaVersion != schemaVersion then
    throw s!"unsupported goal schema: {envelope.schemaVersion}"
  return some envelope.goal

private def collectMessages (messages : MessageLog) : IO
    (Array GoalView × Array Diagnostic × String × Option String) := do
  let mut goals := #[]
  let mut diagnostics := #[]
  let mut output := ""
  let mut protocolError := none
  for message in messages.toArray do
    let dataText ← message.data.toString
    let rendered ← message.toString
    output := output ++ rendered
    match parseGoalLine? dataText with
    | .ok (some goal) => goals := goals.push goal
    | .ok none =>
      diagnostics := diagnostics.push {
        severity := message.severity
        message := dataText.trimAscii.copy
      }
    | .error error => protocolError := some error
  return (goals, diagnostics, output, protocolError)

private def firstErrorMessage (diagnostics : Array Diagnostic) : String :=
  match diagnostics.find? (fun diagnostic => diagnostic.severity == .error) with
  | some diagnostic => diagnostic.message
  | none => "Lean rejected the probe"

private def runProbe (baseEnv : Environment) (request : Request) : IO Response := do
  let (_, messages) ← Elab.process request.source baseEnv {} (some "<formal-agent-driver>")
  let (goals, diagnostics, output, protocolError) ← collectMessages messages
  if let some error := protocolError then
    return {
      requestId := request.requestId
      ok := false
      errorCode := some "protocol_error"
      message := error
      output
    }
  if messages.hasErrors then
    let message := firstErrorMessage diagnostics
    return {
      requestId := request.requestId
      ok := false
      goals := #[]
      diagnostics
      errorCode := some "lean_rejected"
      message := if message.isEmpty then "Lean rejected the probe" else message
      output
    }
  return {
    requestId := request.requestId
    ok := true
    goals
    diagnostics
    message := "ok"
    output
  }

private def handleRequest (baseEnv : Environment) (request : Request) : IO Response := do
  if request.schemaVersion != schemaVersion then
    return failure request.requestId "unsupported_schema"
      s!"unsupported driver schema: {request.schemaVersion}"
  match request.method with
  | "ping" =>
    return {
      requestId := request.requestId
      ok := true
      message := "formal-agent-driver-v1"
    }
  | "probe" | "createState" | "applyState" | "releaseState" =>
    runProbe baseEnv request
  | method =>
    return failure request.requestId "unknown_method" s!"unknown method: {method}"

private def handleLine (baseEnv : Environment) (line : String) : IO Response := do
  match Json.parse line >>= fromJson? with
  | .error error => return failure "" "invalid_request" error
  | .ok request =>
    try
      handleRequest baseEnv request
    catch error =>
      return failure request.requestId "driver_exception" (toString error)

private def writeResponse (stdout : IO.FS.Stream) (response : Response) : IO Unit := do
  stdout.putStr (toJson response).compress
  stdout.putStr "\n"
  stdout.flush

private partial def serve (baseEnv : Environment) (stdin stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  -- An empty read is EOF. Blank JSONL records are deliberately not part of the protocol.
  unless line.isEmpty do
    let response ← handleLine baseEnv line.trimAscii.copy
    writeResponse stdout response
    serve baseEnv stdin stdout

unsafe def run : IO UInt32 := do
  initSearchPath (← findSysroot)
  enableInitializersExecution
  let baseEnv ← importModules
    (loadExts := true)
    (leakEnv := true)
    #[{ module := `FormalAgent.Probe }]
    {}
  serve baseEnv (← IO.getStdin) (← IO.getStdout)
  return 0

end FormalAgent.Driver

unsafe def main : IO UInt32 :=
  FormalAgent.Driver.run
