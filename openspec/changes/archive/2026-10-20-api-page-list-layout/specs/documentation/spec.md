## MODIFIED Requirements

### Requirement: Render dataclass fields and enum members from their docstrings

The system SHALL surface dataclass-field and enum-member docstrings in the
generated reference, laid out as lists rather than tables (`docstring_section_style:
list`), so that no section scrolls sideways on a phone. Dataclass fields MUST render as
a "Fields" list using `griffe-fieldz` plus the section-title extension, once each: the
list item carries the field's anchor (`archivey.ArchiveMember.size`), and the field gets
no separate attribute section. A field's description MUST come from its own attribute
docstring, never from its `default_factory`'s docstring, and an enum default or a class
used as `default_factory` MUST read as source (`AcceleratorMode.AUTO`, `dict()`), not
as `<AcceleratorMode.AUTO: 'auto'>` or `<class 'dict'>`. Enum members MUST render as a
"Members" list of name, value and description via a Griffe extension and custom
template, not the built-in enum "Type" column; so do `ArchiveFormat`'s named
`ClassVar[ArchiveFormat]` instances, with their `(container, stream)` pair as value. `@property`
accessors SHALL fold into the owning class's field list.

#### Scenario: API-object rendering matrix

| Case | Expected |
| --- | --- |
| Enum such as `ListingCost` is generated | Each member appears with name, value, and docstring, e.g. `INDEXED` and its description |
| Dataclass such as `CostReceipt` or `ArchiveMember` is generated | Each field docstring appears once, in the class "Fields" list, and `:attr:` references to the field link to that item |
| Field declared `field(default_factory=dict)` with its own docstring | Its own docstring, and default `dict()` |
| Property accessor is generated | Property appears with fields instead of a separate property section |
| API page at 390 px wide | No section wider than the page |
