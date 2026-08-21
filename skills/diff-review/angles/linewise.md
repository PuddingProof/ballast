APPLIES: always

Read the diff hunk by hunk, then read the whole function around each hunk — an unchanged line still counts if the touched function re-exposes it or leaves it unfixed. For each line, ask what input, state, timing, or platform breaks it. Watch for flipped conditions, off-by-one errors, null or undefined access, a missing `await`, zero treated as false, the wrong variable copied in, an error caught and dropped, and unescaped regex characters.
