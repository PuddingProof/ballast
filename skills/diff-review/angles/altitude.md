APPLIES: almost always — any diff that changes code; skip pure prose/data/config

Check that each change is fixed at the right depth, not patched over with a fragile workaround. Special cases piled onto shared code signal the fix is too shallow — generalize the underlying mechanism instead of adding another special case.
