# KPL Scout knowledge corpus

This directory stores versioned, source-attributed knowledge used by the KPL
Scout research workflow. It is deliberately separate from the SQLite match
database: match, battle, and analytical facts remain available through
deterministic application tools.

## Layout

- `sources/official/`: locally cached primary-source material needed for
  reproducible retrieval.
- `raw/`: source payloads retained for provenance; they are not sent directly
  to the model.
- `sources/project/`: project-owned methodology and structured hero references.
- `sources/secondary/`: non-primary material retained only as explicitly
  labelled context; it must not be presented as an official fact without
  corroboration.
- `metadata/sources.yaml`: the authoritative source register, including status,
  provenance, and collection date.

## Ingestion rules

Every source must have a stable ID, original URL or local provenance, publisher,
source class, collection date, and version/hash when a local snapshot exists.
The registry may contain URL-only sources; download only material needed for
offline retrieval, repeatable evaluation, or a preserved versioned snapshot.
Retrieval output must retain the source ID and must distinguish primary sources
from secondary commentary.

Hero abilities and tactical labels are already structured local artifacts. They
should be exposed through a deterministic `get_hero_profile` tool, rather than
chunked into generic RAG text.

## Initial scope

The initial corpus covers project methodology, structured hero references, the
official hero catalogue, and a citation-ready Tencent patch timeline. KPL rules
remain a required primary source and should be added only when an official,
versioned page is obtained.
