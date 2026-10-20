## 1. Rendering

- [x] 1.1 `docstring_section_style: list`, and enum members rendered as a list in the same layout
- [x] 1.2 Dataclass fields listed once, in the "Fields" list, which carries each field's anchor; their separate attribute sections dropped
- [x] 1.3 A field's own docstring wins over its `default_factory`'s, and defaults read `AcceleratorMode.AUTO` and `dict()` rather than their `repr()`

## 2. Docstrings

- [x] 2.1 Member and field docstrings for the enums and data classes of `types.py`, `config.py` and `diagnostics.py`, with class prose that repeated them trimmed

## 3. Guide pages

- [x] 3.1 Tables on `docs/` pages that scroll sideways at 390 px rewritten as lists, and the `write-user-docs` skill says when a table fits
