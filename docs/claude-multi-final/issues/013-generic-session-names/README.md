# 013 — Session --name is generic and indistinguishable across projects

**Status: resolved** · fixed in 2.11.0 (D47)

## Found (2026-07-30, operator observation)

Every session's `--name` was just `cm:<composition>` (managed) or
`cg:<model>` (ordinary) — the `claude agents` UI showed identical names
for sessions in different projects.

## Fix

`compiler.session_display_name(prefix, cwd)` appends the project
basename: `cm:kimi-sol@occams-agent-flow`, `cg:glm52@claude`. Non-
printable characters are stripped (directory names can be hostile), the
basename is capped at 24 chars, and a root/empty basename keeps the plain
prefix. The launch passes the session's authoritative cwd — the RECORDED
cwd on resume/transition (the session runs there per the cwd lease), the
launcher's cwd on fresh.

Callers: `Runtime.prepare` (managed fresh/resume), `Runtime.prepare_direct`
(ordinary fresh/resume), the transition engine. Compiler-level callers
that pass no `session_cwd` (tests, goldens) keep the plain form, so the
golden argv files are unchanged by design.

Resume-by-name is preserved for the qualified form (cross-family review
must-fix): the native exit hint prints the `--name` value verbatim, so
`_resolve_resume_target` matches `cm:<composition>@<project>` per record
(composition + authoritative cwd) alongside UUIDs and plain composition
names, with the same ambiguity listing when several records share one
qualified name.

Pins: `SessionDisplayNameTests` (7 unit tests incl. compile-level
threading + backward compatibility), `SessionNameWiringTests` (fresh
managed, resume-uses-recorded-cwd, direct), and the qualified-name
`_resolve_resume_target` tests (direct, recorded-cwd, ambiguity,
unknown, end-to-end).
