# Machine schemas

- `contracts-v1.json` describes the deterministic tagged output of common frozen
  `Contract` DTOs and enumerates the full v1 contract family.
- `events-v1.json` describes `KernelEvent` schema version 1 and checks the
  event-type/payload discriminator pairing.
- `config-v1.json` describes a plain-JSON interchange form corresponding to
  `KernelConfig`, provider profiles, engine options, and MCP server settings.

The config schema is not a loader: convert validated objects to Python
`KernelConfig`, `ProviderProfile`, `ProviderRoleMapping`, `EngineOptions`,
`McpServerConfig`, and immutable `JsonObject` explicitly.

Validate schema syntax and checked examples from the repository root:

```powershell
python -m examples.kernel.validate_schemas
```
