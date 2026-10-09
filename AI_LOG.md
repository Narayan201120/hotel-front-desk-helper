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

## 2026-10-09, stage 1 misses found in review

Wrong: the stage 1 fact sheet and test set missed two real guest
situations. Service animal questions had no fact and no route, so the bot
would have answered from the pet policy or guessed. Third-party bookings
had no mention in the cancellation fact, so the bot would have stated our
policy as if it applied to Expedia-style bookings.
Caught by: user review of stage 1 before stage 2 started, not by any
automated check. The 40-question set passed its own validation (counts,
routes, fact references) and still missed them, because validation only
checks internal consistency, not coverage of the problem space.
Fix: commit b8b1285 added the service_animals fact, the third-party
sentences in the cancellation fact, and tests t11, t12, t13.
