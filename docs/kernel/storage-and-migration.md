# Storage and migration

## SQLite ownership

`SQLiteDatabase` owns one shared `aiosqlite` connection and serializes all reads
and writes through one operation lock. File databases enable foreign keys,
5-second busy timeout, `synchronous=NORMAL`, and WAL; in-memory databases do not
enable WAL. Writes use `BEGIN IMMEDIATE`, rollback on any `BaseException`, and
otherwise commit. The current schema version is 1.

| Table / virtual table | Purpose |
|---|---|
| `kernel_schema` | kernel schema version |
| `sessions` | tagged `SessionRecord` JSON, counts, timestamps, single active marker |
| `config_revisions`, `config_state` | immutable revision snapshots and current pointer |
| `workspace_history`, `workspace_state` | revisioned workspace records and current pointer |
| `memory_entries`, `memory_tags` | memory records, unique namespace/key and exact tags |
| `memory_fts` | external-content FTS5 index, synchronized by insert/update/delete triggers |

Repository DTOs are stored through deterministic tagged contract JSON. A corrupt
record returns a typed failure or is skipped by FTS search; there is no automatic
repair. All repositories sharing one `SQLiteDatabase` are serialized even in WAL
mode, so one long query blocks other repository work in that kernel.

## Session repository

Session save is an upsert. Passing `active=True` clears the previous active
marker in the same transaction. Delete of a missing session returns
`session_not_found`. `context_used_tokens` is repository metadata used by the
legacy importer and is not a `SessionRecord` field.

## Configuration and workspace revisions

Configuration save writes the snapshot and advances the singleton pointer. With
`create_backup=False`, other revisions are deleted. Restore moves the pointer to
an existing revision. `ConfigurationService` creates a new monotonic revision
when restoring through its public API.

Workspace validation resolves the path strictly, requires a directory, and uses
a temporary file write probe. Apply writes history plus the current pointer in
one transaction; repository rollback is implemented as apply of the prior
record. Service-level transactions additionally update runtime participants and
mark degraded if compensating rollback fails.

## Memory and blobs

`SQLiteMemoryStore` scopes search by namespace, deduplicates tag filters, uses
quoted Unicode word tokens for FTS, ranks by BM25 then update time, and clamps
the limit to 0..100. `(namespace, key)` is unique across IDs. Searchable text is
derived from text/reasoning and useful metadata in other content blocks.

`BlobStore` is a separate content-addressed filesystem store. It uses SHA-256,
atomic replace, byte caps, optional expected digest, and resource IDs of the form
`sha256:<hex>`. The default `build_kernel` composition does not expose or
instantiate a blob store.

## Legacy JSON session migration

`LegacyJsonImporter` is a one-way importer; it does not rewrite the source
directory.

```python
database = await SQLiteDatabase("kernel.db").open()
repository = SQLiteSessionRepository(database)
importer = LegacyJsonImporter(repository)
result = await importer.import_directory("legacy/sessions")
print(importer.warnings)
```

Rules enforced by the importer:

- session filenames/IDs must be 32 lowercase hexadecimal characters;
- an index entry must name exactly `<id>.json`;
- missing/corrupt indexes trigger orphan scanning;
- session ID must match its filename and history must start with system;
- malformed messages are skipped with warnings; deterministic UUIDv5 IDs are
  generated when a message lacks an ID;
- legacy tool calls/results, runtime-state messages, summaries, compression
  count, timestamps, and context-used metadata are converted where possible;
- invalid files are isolated as warnings and do not abort other sessions;
- the indexed active session is preserved; otherwise the first valid import is
  marked active;
- no valid imported session returns `not_found`.

Migration is not idempotence-tracked: rerunning upserts the same session IDs.
Keep the source until imported counts, warnings, active session, and representative
histories have been verified.
