# Test fixtures

Small, curated binary samples used by the test suite. Large real-world
`.pst` / `.ost` dumps are **not** committed (see `.gitignore`).

| file | origin | used by |
|------|--------|---------|
| `two-files.tnef` | tnefparse project test corpus (MIT) | `test_tnef.py` |
| `body.tnef` | tnefparse project test corpus (MIT) | `test_tnef.py` |

To add a synthetic `.msg` / `.pst` fixture, see the open follow-up task; a
generator script should live next to this file.
