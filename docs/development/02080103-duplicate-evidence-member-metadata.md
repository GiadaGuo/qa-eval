# 02080103 duplicate Evidence member metadata

`text_duplicate_degree@2.0.0` keeps its duplicate detection and measurements
unchanged. The `DUPLICATE_GROUPS` Evidence schema advances from the previously
referenced `2.0.0` shape to the additive `2.1.0` shape. Exact and near-candidate
groups retain all existing fields and add an ordered `members` array. Each member
contains only `sample_id`, `document_id`, `source`, and `collection_batch`;
missing values are explicit JSON `null` values.

## Canonical field paths

The Calculator reads already-normalized `SampleRecordV2` fields. It does not
inspect titles, text, dataset version IDs, or source locators to infer provenance.
The supported paths, in precedence order, are:

- `document_id`: `metadata.document_id`, `metadata.article_id`,
  `metadata.policy_id`.
- `source`: `source.attributes.source`,
  `source.attributes.origin_name`, `source.attributes.source_name`,
  `metadata.source`, `metadata.origin_name`, `metadata.extra.source`.
- `collection_batch`: `source.attributes.collection_batch`,
  `source.attributes.batch_id`, `metadata.collection_batch`,
  `metadata.batch_id`, `metadata.extra.collection_batch`.

The current `policy.policy` mapping supplies the business source field at
`metadata.extra.source`; the older `jhsjk_article` mapping supplies the business
document ID at `metadata.article_id` and may supply origin information through
source attributes. No current canonical mapping supplies a collection batch, so
that field remains `null` unless an input mapping explicitly places it at one of
the paths above. `source.source_record_id` is intentionally not used as
`document_id`, because a source-record identity is not necessarily a business
document identity and commonly equals `sample_id`.

## Compatibility and lifecycle

The Evidence Artifact remains linked through `Dataset Version -> Evaluation Run
-> Metric Result -> Evidence Artifact`; `dataset_version_id` is therefore not
duplicated in groups or members. `quality_result_service.py` continues to consume
the retained group fields and ignores the additive member data. Consequently,
legacy Evidence without `members` remains readable and exact/candidate Quality
Issue creation is unchanged.

Input JSONL is read once as a binary stream while the existing SHA-256 input
fingerprint is accumulated. Metadata is attached to the existing in-memory
feature entry and is omitted from `sample_features.jsonl`; no second record scan,
body copy, HTML copy, or additional full-record index is introduced. Artifact
growth is limited to four short scalar fields per member occurrence in exact and
near groups.

This change does not claim semantic duplicate detection, confirmed near
duplicates, byte-level source-file duplication, cross-source/batch metrics, or
ground-truth recall.
