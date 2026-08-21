APPLIES: the diff touches a language with well-known traps (JS/TS, Python, Go, SQL, C/C++, shell…) and uses the constructs involved (skip config, prose, or a language without them)

Look for the well-known traps of its language or framework — JS treating zero as false, `==` coercion, a loop variable caught by closure; Python mutable default arguments, late-binding closures; Go nil-map writes, range-variable capture; SQL injection; timezone/DST drift; float equality. Flag any the diff introduces.
