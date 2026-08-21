APPLIES: the diff adds or modifies a type that wraps or delegates to another (skip if no such type is touched)

When the diff adds or changes a type that wraps another — cache, proxy, decorator, adapter — check that every method calls through to the wrapped object, never back through a registry, session, or global that would route into the wrapper itself and loop. Also confirm the wrapper still forwards every method callers rely on.
