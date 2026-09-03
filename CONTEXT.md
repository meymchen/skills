# Agent Skill Distribution

This context defines the language used to distribute this repository's reusable agent capabilities across supported coding-agent hosts.

## Language

**Skill**:
A reusable agent capability with its own instructions and, when needed, supporting resources.
_Avoid_: Plugin, command

**Standalone Skill**:
A Skill distributed from the existing Skill collection independently of either Plugin Marketplace. Adding Marketplace support does not change a Standalone Skill.
_Avoid_: Plugin

**Plugin**:
An independently installable unit exposed by a Marketplace. A Plugin may contain a Skill together with host-specific metadata or resources.
_Avoid_: Skill, package

**Plugin Template**:
A non-installable recipe used to create a Plugin for both supported Hosts. It is excluded from Marketplace catalogs and becomes installable only after it is instantiated.
_Avoid_: Example plugin, starter plugin

**Plugin Generator**:
The repository tool that instantiates the Plugin Template and keeps the new Plugin's identity consistent across Host-specific metadata.
_Avoid_: Installer

**Draft Plugin**:
A generated Plugin that exists in the repository but is not listed in either Marketplace.
_Avoid_: Unpublished Skill

**Published Plugin**:
A Plugin listed in both Host Marketplaces through an explicit publication action. A documented Host compatibility exception may make it unavailable on one Host.
_Avoid_: Generated plugin

**Marketplace**:
A publicly accessible, repository-hosted catalog from which a supported Host can discover and install Plugins.
_Avoid_: Official marketplace, registry

**Marketplace-ready Repository**:
A repository containing valid Host catalogs, a Plugin Template, generation tooling, and verification, but not necessarily a Published Plugin.
_Avoid_: Marketplace with plugins

**Host**:
A coding-agent product that discovers and runs Plugins. The supported Hosts are Claude Code and Codex.
_Avoid_: Client, platform
