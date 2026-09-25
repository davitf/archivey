# Design — deferred stream raises

Raising at the emit site is what the deferral exists to avoid, and a call can raise only
one exception, so later escalations in the same operation coalesce into the first. The
alternative, keeping the later ones and raising them on the next call, would raise on a
call that met no condition itself.
