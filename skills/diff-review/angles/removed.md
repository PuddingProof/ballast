APPLIES: the diff deletes or replaces lines (skip a pure-addition diff)

For each line the diff removes or replaces, work out what rule or behavior it enforced, then look for where the new code enforces it again. If you can't find it, flag it: a guard that's gone, an error path no longer handled, a validation that's been loosened, or a deleted test that covered a real case.
