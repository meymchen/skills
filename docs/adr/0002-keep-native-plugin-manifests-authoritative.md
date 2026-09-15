# Keep native Plugin manifests authoritative

Store one Claude Code manifest and one Codex manifest in every Plugin, with CI checking that their shared identity fields agree. Do not introduce a third neutral manifest: although it could generate both Host formats, it would hide native capabilities behind a repository-specific abstraction and become another schema to maintain. The Plugin Generator accepts shared metadata once and renders both native files, while later Host-specific edits remain possible.

Repository tests enforce cross-Host invariants. Every native smoke run resolves the latest stable Host releases from npm, records their versions, and uses them to validate and install a generated fixture. Plugins and Skills must adapt to the latest Host releases, including releases discovered by the weekly schedule. Native packages are unpacked without npm lifecycle scripts and run in an isolated user directory.
