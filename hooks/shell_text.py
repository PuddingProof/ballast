"""Shared helpers for the shell-guards modules: literal stripping.

Pure functions, no I/O at import. Imported by package_install_guard.py and
process_lifecycle_guard.py (run together by shell-guards.py).
"""

import re


# Shell wrappers whose quoted body IS the real command. Unwrapped BEFORE the quote strippers,
# otherwise `bash -c "npm install evil-pkg"` has its whole body erased as a quoted literal and
# the verb never reaches the guard's patterns.
UNWRAP = re.compile(
    r"\b(?:bash|sh|zsh|dash|pwsh|powershell(?:\.exe)?|cmd(?:\.exe)?)\b"
    r"[^'\"]{0,40}?\s(?:-c|-Command|/c|/C)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)


def neutralize(cmd):
    """Strip text that is DATA, not command: heredoc bodies, quoted strings, $() subshells.

    A verb inside a commit message, an echoed string, or a grep pattern must not fire. Shell
    wrappers are unwrapped FIRST so their quoted body survives as command text. Heredoc bodies
    (<<DELIM ... DELIM, incl. <<'DELIM' / <<"DELIM" / <<-DELIM) are stripped before the quote
    strippers, because a quoted delimiter (<<'EOF') would otherwise be mangled and the
    closing-delimiter match would break. The closing delimiter is matched at line start
    (MULTILINE); with no closing line the pattern simply doesn't match and the body is left
    as-is (fail-safe).
    """
    s = UNWRAP.sub(lambda m: " " + m.group(2) + " ", cmd)
    s = re.sub(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1.*?^[ \t]*\2[ \t]*$", "", s,
               flags=re.DOTALL | re.MULTILINE)
    s = re.sub(r'""".*?"""', "", s, flags=re.DOTALL)   # triple-quoted
    s = re.sub(r"\$\(.*?\)", "", s, flags=re.DOTALL)    # $(...) subshells
    s = re.sub(r'"[^"]*"', "", s)                        # "double"
    s = re.sub(r"'[^']*'", "", s)                        # 'single'
    return s
