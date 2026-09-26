# documentation — docstring cross-references delta

## ADDED Requirements

### Requirement: Docstring cross-reference roles render as links

Docstrings SHALL write cross-references as Sphinx roles (`:class:`, `:meth:`, `:func:`,
`:attr:`, `:data:`, `:exc:`, `:mod:`, `:obj:`, `:const:`, `:any:`, with an optional `py:`
domain). A Griffe extension SHALL render each role as a link when its target has an
anchor on the site, and as inline code when it does not. A role MUST NOT reach the
published site as literal text. A target written without a module path SHALL resolve
in the scope of the object whose docstring holds it, and a target reached through its
defining module SHALL link to the public `archivey.<Name>` path the API page documents.

#### Scenario: role rendering

| Case | Expected |
| --- | --- |
| Docstring holds `:class:`~archivey.ArchiveMember`` | API page shows `ArchiveMember` as inline code linking to the `ArchiveMember` entry |
| Docstring holds a role whose target has no anchor on the site (a standard-library function) | API page shows the target as inline code with no link and no role text |

## MODIFIED Requirements

### Requirement: Documentation build is verified in CI

The system SHALL build docs in CI with `mkdocs build --strict`. Broken
cross-references, removed/renamed public symbols, and rendering warnings MUST fail
the build.

A docstring role is an optional reference, which the strict build does not report
when it fails to resolve. CI SHALL therefore also check the built site. The check
MUST fail when a role is left as text, and when a role's target does not resolve to
a link, unless that target is on a recorded list of targets known to have no anchor.
The check MUST also fail when a target on that list no longer occurs.

#### Scenario: strict-docs CI

| Case | Expected |
| --- | --- |
| Docs reference a missing symbol or unresolved cross-reference | Strict docs build fails and surfaces the drift in CI |
| A docstring role's target stops resolving (a renamed symbol) and is not on the recorded list | The built-site check fails and names the target |
| A docstring uses a role spelling the extension does not render | The built-site check fails and shows where the role text appears |
