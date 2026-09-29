# Choosing how to read

With no options, `open_archive` lets you read any member at any time, one at a time. That
suits most programs. Three options change it for the cases where the default is slow or not
enough: `streaming`, `seekable_members` and `concurrent_members`.
