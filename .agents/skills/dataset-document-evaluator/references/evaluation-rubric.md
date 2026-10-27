# Evaluation rubric

## Shared decision rules

Evaluate semantic sufficiency, not keyword presence. Use these item states:

- `SATISFIED`: meaningful content reaches the item's minimum standard and is not contradicted by supplied evidence.
- `INSUFFICIENT`: the topic is mentioned, but content is vague, incomplete, non-actionable, or only a placeholder.
- `MISSING`: the document contains no relevant content after inspecting the whole document.
- `UNVERIFIABLE`: a factual claim needs confirmation to distinguish genuine absence from omission, and available evidence cannot resolve it.
- `ERROR`: evaluation could not be completed because parsing or execution failed. This is not a quality judgment.

When a fact genuinely does not exist, explicit and appropriately quantified disclosure may satisfy only the items whose rules allow it. A document's unsupported “none” statement does not prove absence.

## 0101 Basic-information completeness

### 0101-01 Dataset scale

`SATISFIED` requires at least one explicit quantity with an identifiable unit and object, such as records, samples, files, bytes, or characters. A bare adjective such as “large” is insufficient. When supplied evidence includes a row count, compare it with the document.

### 0101-02 Format specification

`SATISFIED` requires the file format plus enough encoding, record-boundary, schema, or reading information for a user to interpret the data. Merely naming JSON, CSV, or another format without usage detail is insufficient.

### 0101-03 File structure

`SATISFIED` requires the files or directories and their main purposes. A single unexplained or ambiguous path is insufficient.

### 0101-04 Access channel

`SATISFIED` requires an actionable location, URL, platform entry, repository path, or permission-acquisition method. “The data is accessible” is insufficient.

### 0101-05 Technical support

`SATISFIED` requires an actionable person, team address, email, issue tracker, project entry, or defined support process. “Contact the maintainer,” “TBD,” or “none” without a usable channel is insufficient.

## 0102 Content-characteristic completeness

### 0102-01 Modality

`SATISFIED` requires an explicit modality—text, image, audio, video, or multimodal—and a description of the principal content form.

### 0102-02 Data distribution

`SATISFIED` requires at least one meaningful distribution with counts or proportions and its basis, such as length, label, language, time, or source distribution. A total count alone is insufficient.

### 0102-03 Label-category statistics

For labeled data, `SATISFIED` requires label categories and counts or proportions. For genuinely unlabeled data, it requires an explicit unlabeled count/proportion and supporting schema or evidence. “There are no labels” without quantification or support is insufficient.

### 0102-04 Sample example

`SATISFIED` requires at least one representative example showing the actual schema or content form. Placeholder pseudocode or field names alone are insufficient. Avoid requiring disclosure of unnecessary sensitive content.

### 0102-05 Limitations

`SATISFIED` requires at least one concrete coverage gap, bias, quality risk, representativeness constraint, or usage limitation. “There may be limitations” is insufficient.

## 0103 Construction-process completeness

### 0103-01 Data source

`SATISFIED` requires an identifiable source system, organization, database/table, public channel, or original carrier. Generic “web data” or “business data” is insufficient.

### 0103-02 Collection method

`SATISFIED` requires how data was obtained—export, crawl, API, upload, or manual collection—and material collection conditions when relevant. Source alone is insufficient.

### 0103-03 Processing flow

`SATISFIED` requires major cleaning, conversion, filtering, deduplication, retention, or validation steps. If no processing occurred, an explicit statement of unchanged retention and boundaries can satisfy the item.

### 0103-04 Annotation specification

For labeled data, `SATISFIED` requires label definitions or operational rules and quality control. For genuinely unannotated data, an explicit statement of that fact plus field boundaries can satisfy the item. If supplied evidence shows labels, an “unannotated” statement is not satisfied.

### 0103-05 Version-control record

`SATISFIED` requires at least a version identifier, time, and change summary. Multiple versions should describe their relationship when that information is available. “Latest version” alone is insufficient.

## 0104 Application-description completeness

### 0104-01 Usage license

`SATISFIED` requires a named license or authorization basis, or a concrete scope covering applicable departments/users, permitted purposes, redistribution, and material restrictions. Vague “internal use only” wording without departments and scope is insufficient.

### 0104-02 Target application scenario

`SATISFIED` requires a concrete task, target user, or business use. “For AI” or “for research” alone is insufficient. Explicit prohibited or high-risk uses strengthen but are not mandatory.

### 0104-03 Evaluation method

`SATISFIED` requires what is evaluated, how it is calculated or verified, and the necessary input or measurement basis. “Conduct quality evaluation” is insufficient.

### 0104-04 Benchmark result

`SATISFIED` requires a task, tested method or model where applicable, metric, and observed result. “Not tested,” a planned protocol without results, or a data-quality statistic mislabeled as downstream model performance is insufficient.

### 0104-05 Typical application case

`SATISFIED` requires a concrete use with input and processing or expected output/value. A scenario name alone is insufficient. Clearly label a proposed case as proposed rather than a completed production record.

## Evidence and remediation

- Prefer the shortest excerpt that proves the judgment; include all necessary lines when content is distributed.
- For conflicts, cite both the document and external evidence and explain the mismatch.
- `AUTO_FIXABLE`: formatting, structure, wording, or reorganization of facts already present.
- `DATA_DERIVABLE`: a deterministic calculation from supplied dataset evidence is required before adding content.
- `REQUIRES_HUMAN_INPUT`: licensing, authorization, ownership, support contacts, business intent, or facts not derivable from supplied evidence.
