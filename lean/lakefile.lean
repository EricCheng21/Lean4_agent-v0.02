import Lake
open Lake DSL

package «formal_agent» where

lean_lib FormalAgent where

lean_exe formal_agent_driver where
  root := `FormalAgent.Driver
  supportInterpreter := true
