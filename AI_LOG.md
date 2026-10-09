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

## 2026-10-09, greedy number-word suffix regex

Wrong: the first version of the number-word normalizer used a greedy
`^([A-Z-]+)(AM|PM)?$` split, so the token "eleven AM" (spaces stripped to
"ELEVENAM") parsed as one unknown word instead of 11 + AM. A correct fact
answer, "Standard check-out is eleven AM Eastern", was blocked.
Caught by: the new smoke check `test_word_time_passes_when_correct`, before
commit. The check failed with reason unknown-number-time-fee:eleven AM.
Fix: non-greedy prefix split in commit 2c5b290, so a trailing AM/PM splits
off instead of being swallowed.

## 2026-10-09, first eval run measured quota, not the bot

Wrong: the first full 40-question eval ran questions back to back and hit
the free key's per-minute quota, so 37 of 40 fell back to human. The
summary looked like a bot failure. It was not: no reply was blocked and no
fact was invented, which is the fallback working as designed.
Caught by: the shape of the result itself (near-total human fallback with
zero blocks), confirmed by a direct probe returning 429
RESOURCE_EXHAUSTED, then succeeding after 75 seconds idle.
Fix: added --pause-secs to tests/run_eval.py and redid the run spaced at
12 seconds per question.

## 2026-10-09, provider changed from Gemini to Groq

Wrong: the eval could not run on Gemini. The free key's per-minute quota
tripped after a handful of calls, so two full runs measured the quota
instead of the bot (37 and 4 fallbacks to human, zero model answers).
Caught by: the shape of the results, confirmed by direct 429 probes.
Fix: switched to a Groq key on a fresh account in commit d8c97c5 and
re-ran the eval there.
