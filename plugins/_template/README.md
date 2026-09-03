# {{display_name}}

This draft plugin was generated from the repository's dual-host template.

## Development

Validate it from the repository root:

```console
uv run --script scripts/create_plugin.py check {{plugin_name}}
```

Publish it to both marketplace catalogs after it is ready:

```console
uv run --script scripts/create_plugin.py publish {{plugin_name}}
```

After changing metadata on a published plugin, synchronize its catalog entries:

```console
uv run --script scripts/create_plugin.py sync {{plugin_name}}
```
