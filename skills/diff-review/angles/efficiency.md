APPLIES: the diff adds computation, I/O, loops, or startup/hot-path work (skip config, prose, trivial)

Flag wasted effort the diff adds: computation or I/O repeated needlessly, independent steps run one after another instead of together, or blocking work placed in startup or a hot path. Also flag long-lived objects built from a closure or captured scope — they keep that whole scope alive for as long as they exist, leaking memory if it holds anything large; a class or struct copying only the needed fields avoids this. Point to the cheaper approach.
