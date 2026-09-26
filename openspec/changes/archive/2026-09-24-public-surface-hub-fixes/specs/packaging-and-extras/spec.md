## MODIFIED Requirements

### Requirement: Version metadata exposure

The system SHALL expose the installed version as `archivey.__version__`, resolved
from installed distribution metadata via `importlib.metadata` rather than a
hard-coded string. The lookup SHALL run on first access to the attribute (a
module-level `__getattr__`), not at `import archivey`, and its result SHALL be
cached.

#### Scenario: installed-version metadata

| Case | Expected |
| --- | --- |
| Caller reads `archivey.__version__` | Returns the version recorded in installed package metadata, e.g. `"0.2.0"` |
| `import archivey` without reading `__version__` | `__version__` is not yet bound in the module namespace |
