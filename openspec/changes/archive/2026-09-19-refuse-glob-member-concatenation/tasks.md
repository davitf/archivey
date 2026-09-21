# Tasks — refuse glob-member concatenation

## 1. The setting

- [x] 1.1 Add `ArchiveyConfig.rar_allow_glob_member_concatenation`, default `False`,
      documenting what the read costs and that a non-matching glob name is unaffected.

## 2. The refusal

- [x] 2.1 In `_open_member`, raise `UnsupportedFeatureError` when `glob_prefix` is
      non-zero and the flag is unset. Placed after `_unrar_glob_prefix` so the message
      can name the byte count, and before the spawn so nothing is started.
- [x] 2.2 Name the flag in the message, so the escape route needs no source reading.

## 3. Pins

- [x] 3.1 `test_glob_member_with_earlier_matches_is_refused` over all three wildcard
      fixtures.
- [x] 3.2 `test_glob_member_matching_nothing_else_still_reads` — `only*.dat`, no flag.
      The boundary the refusal must not overshoot.
- [x] 3.3 `test_glob_concatenation_flag_names_itself_in_the_refusal`.
- [x] 3.4 `test_wildcard_nonsolid_stream_members_hits_the_refusal` — characterization,
      labelled as such, with the condition for flipping it.
- [x] 3.5 The four existing skip tests opt into the flag via `_ALLOW_GLOB_CONCAT`: they
      pin the skip, which is still live behind it.
