# docs-ia-unpublish-maintainer-tree — move maintainer docs off the published site

**Status:** Ready to implement. Depends on nothing; blocks the follow-up prose change that splits pages and writes the how-it-works page. Not breaking. Effort: medium, but almost entirely mechanical.

**Why it matters:** the published documentation site is about nine thousand three hundred lines, and roughly three quarters of it was written for maintainers rather than users. Someone searching the docs for PPMd lands in a six hundred line upstream investigation report. The audit that found this also found the inverse problem, which matters more: the safe extraction page, which carries the project's first vision claim and is backed by the largest spec in the tree, is the thinnest page on the site. This change fixes the first half — where things live — and leaves the second half to a follow-up.

**What it does:** moves the internal directory, the historical grab-bag, the raw decision log, and the plan and ideas files out of the site and into a new unpublished dev-docs tree; deletes four leftover moved-to stubs at the repository root; repoints about a hundred references, two of which are runtime error messages a user can actually see; and adds a CI check that fails when a page under docs has no navigation entry. That last one is not hypothetical: the strict docs build today prints six such pages and still exits zero.

**Decided:** the whole migration is split in two, and this is the move-only half, because a rename-only diff can be checked by reading filenames while a move-plus-rewrite diff cannot. Published pages stop linking into maintainer material entirely — prefer inlining the sentence, otherwise link the file on GitHub. The known-issues register moves whole rather than being split, with its triage recorded as an explicit follow-up.

**Your call later:** one deviation to confirm. The recorded decision said this change should create an empty how-it-works page and its navigation slot, with the content following later. It does not, because an empty published page breaks the invariant this change exists to establish, on its first day. Nothing depends on the page in the meantime. Say the word and the stub is one commit.

**Bottom line:** the cheap, reviewable half of the docs reorganization, and it should land before the point-two-oh tag freezes the README's URLs.
