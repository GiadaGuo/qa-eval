# Policy / jhsjk_article documentation test fixture

Snapshot 20261022T085048Z, canonical version
019a2a10-0000-7000-8000-000000000102.
Source facts come from the canonical Manifest, snapshot Profile and the
SampleRecord 2.0 schema. Sections marked SYNTHETIC apply to the test only.

## 0101 Basic information

- Scale: 14,960 normalized records in this snapshot.
- Format: UTF-8 JSON Lines, one JSON object per line, SampleRecord 2.0.
- Files: raw_snapshot.jsonl stores exported rows; sample_records.jsonl stores
  normalized data; dataset_manifest.json lists artifacts and checksums.
- Access: see data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/
  canonical-v1/dataset_manifest.json in the local QA checkout.
- Support (SYNTHETIC): submit a ticket to policy-qa@example.invalid with the
  snapshot version, record ID and reproduction steps.

## 0102 Content characteristics

- Modality: text, primarily plain-text policy article bodies.
- Distribution: Unicode character length of the raw text field is zero for
  32 of 14,960 records and at least 5,000 for 671 of 14,960 records.
- Labels: 0 of 14,960 rows have task labels in the export. The raw schema
  has sample_id, source, text and text_is_null fields only.
- Example (SYNTHETIC, follows raw schema): {"sample_id":"demo-1","source":{"type":"mysql"},"text":"Example policy notice","text_is_null":false}
- Limitation: a single MySQL table and capture date limit historical and
  source coverage. 32 rows have empty text.

## 0103 Construction process

- Source: MySQL table policy.jhsjk_article.
- Collection: repeatable-read snapshot, ascending unique id keyset pages,
  captured on 2026-10-22 at 08:50:48 UTC.
- Processing: require sample_id, map text to article.body.plain, keep source
  as passthrough metadata, ignore text_is_null and validate SampleRecord 2.0.
- Annotation: no annotation was added in this export/normalization pipeline;
  source metadata and the null-text flag are operational fields, not labels.
- Version history: 20261022T085048Z (2026-10-22) freezes the table export;
  canonical-v1 derives normalized SampleRecords from that snapshot.

## 0104 Application instructions (all entries SYNTHETIC)

- License: this fictional QA scenario assigns CC BY 4.0 to its synthetic data.
- Scenario: policy analysts search article text to triage permit guidance.
- Evaluation: use 20 labeled synthetic queries against the mock index.
  Measure recall at five as retrieved relevant articles / all relevant articles.
- Benchmark: in that synthetic scenario BM25 achieved recall at five = 0.75
  over the 20 queries. This is an invented test value.
- Case: index the article bodies, query "temporary permit", and return five
  ranked article IDs with snippets for a reviewer to inspect.
