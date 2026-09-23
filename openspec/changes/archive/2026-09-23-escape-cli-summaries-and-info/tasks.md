## 1. Fix the print sites

- [x] 1.1 `info`: escape every value; `path` through `escape_path`; `open:` through
      `format_error_detail`
- [x] 1.2 `extract`: escape the summary destination, the hoist lines and the wrapper
      lines; move the collision lines from escaping a native path to `escape_path`

## 2. Tests

- [x] 2.1 Red-green: the summary and the hoist line with a hostile single root (ANSI on
      POSIX, U+2028 everywhere)
- [x] 2.2 Red-green: a wrapper named after an archive file with a non-printable character
- [x] 2.3 Red-green: `info` prints a hostile ZIP comment escaped on stdout; every `info`
      value goes through the escape
- [x] 2.4 An ordinary comment is unchanged; the `open:` line is not escaped twice

## 3. Land the spec

- [x] 3.1 Archive this change into `openspec/specs/cli/spec.md`
