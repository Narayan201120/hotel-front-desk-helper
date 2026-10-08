SAMPLE DATA: invented hotel, no real guest data.

# Hotel front desk helper

Placeholder. Full README arrives in stage 5.

## Run steps

TODO in stage 5. Target: run locally in 5 commands.

## What it does

TODO in stage 5.

## What is cut and why

TODO in stage 5.

## Test results

TODO in stage 4.

## Assumptions

- Stage 2: the store is in-memory, so restarts lose chats, request cards, and the human queue. Fine for a demo, stated here instead of hidden.
- Request acknowledgments are fixed templates, not model text, so the bot cannot accidentally promise a late checkout or early check-in.
- Human handoffs are canned, except two sentences taken verbatim from the sheet (service animals, third-party bookings).
- The number check covers digit tokens, $ amounts, and clock times. Number words ("eleven") can slip past it. The phrase prompt plus temperature 0 are the mitigation.

## Next steps

TODO in stage 5.
