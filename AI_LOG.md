# AI log

Record of wrong outputs and how they were caught. One honest entry minimum.

## 2026-10-08, stage 2 setup

Wrong: piped the install command into `tail` on Windows PowerShell, where
`tail` does not exist. The install itself ran, but the verification half of
the command failed, so the output proved nothing.
Caught by: the shell error in the command result.
Fix: re-verified directly with `py -c "import fastapi, anthropic, yaml,
httpx"`, which printed `deps ok`. Lesson applied: verify with the
interpreter, not Unix pipe habits, on this machine.
