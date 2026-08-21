APPLIES: a changed function has callers outside the diff — a quick grep confirms (skip a self-contained or local change)

For each function the diff changes, grep for its name to find every caller, then check whether any break: a new precondition, a different return shape, a new exception, a new dependency on timing or order. Also check callees — does another change in the same diff make a call unsafe?
