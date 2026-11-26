from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qa_eval.application.mapping_templates import MappingTemplateResolver
from qa_eval.application.normalization.input_resolver import InputResolver
from qa_eval.application.normalization.record_reader import (
    RawRecordManifest,
    RawSampleRecord,
)
from qa_eval.contracts.logical_inputs import (
    CalculatorInputContract,
    FieldBinding,
    InputSlot,
    MappingDraftItem,
    MappingRevision,
    Representation,
    SemanticDefinition,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.mapping_revision import MappingRevisionModel
from qa_eval.paths import PROJECT_ROOT

TEMPLATE_SEMANTICS = {"policy.policy": {
    "title": "document.title",
    "body-html": "document.body.html",
    "body-plain": "document.body.plain",
    "primary_url": "document.source.url",
    "publisher.organization": "policy.publisher",
    "document_number.filenumber": "policy.document_number",
    "published_at": "policy.published_at",
    "effective_at": "policy.effective_at",
    "formatted_at": "policy.formatted_at",
    "policy_level.level": "policy.level",
    "business.validity": "policy.validity",
    "business.status": "policy.status",
    "business.is_public": "policy.public_status",
    "attachments.name": "policy.attachment.name",
    "attachments.url": "policy.attachment.url",
    "type": "policy.document_type",
}}


@lru_cache(maxsize=1)
def builtin_semantics() -> tuple[SemanticDefinition, ...]:
    directory = PROJECT_ROOT / "configs" / "semantics"
    return tuple(
        SemanticDefinition.model_validate(item)
        for path in sorted(directory.glob("*.json"))
        for item in json.loads(path.read_text(encoding="utf-8"))
    )


class MappingRevisionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def confirm(self, revision: MappingRevision) -> str:
        version = self.session.get(DatasetVersionModel, revision.dataset_version_id)
        if version is None or version.status != "PUBLISHED":
            raise ValueError("mapping confirmation requires a published DatasetVersion")
        source = (version.artifact_refs or {}).get("raw_record_manifest")
        if source is None:
            raise ValueError("mapping confirmation requires a v3 record Manifest")
        manifest = RawRecordManifest.model_validate(source)
        if (
            manifest.dataset_version_id != revision.dataset_version_id
            or manifest.schema_fingerprint != revision.schema_fingerprint
        ):
            raise ValueError("mapping schema does not match the published Manifest")
        digest = revision.fingerprint()
        key = (revision.dataset_version_id, digest)
        if self.session.get(MappingRevisionModel, key) is not None:
            return digest
        try:
            with self.session.begin_nested():
                self.session.add(MappingRevisionModel(
                    dataset_version_id=revision.dataset_version_id,
                    revision_digest=digest,
                    document=revision.model_dump(mode="json"),
                ))
                self.session.flush()
        except IntegrityError:
            if self.session.get(MappingRevisionModel, key) is None:
                raise
        return digest

    def get(self, dataset_version_id: UUID, digest: str) -> MappingRevision | None:
        stored = self.session.get(MappingRevisionModel, (dataset_version_id, digest))
        if stored is None:
            return None
        value = MappingRevision.model_validate(stored.document)
        if value.fingerprint() != digest:
            raise ValueError("stored mapping checksum mismatch")
        return value


    def list(self, dataset_version_id: UUID, *, limit: int, offset: int) -> list[dict]:
        rows = self.session.scalars(
            select(MappingRevisionModel)
            .where(MappingRevisionModel.dataset_version_id == dataset_version_id)
            .order_by(
                MappingRevisionModel.confirmed_at.desc(),
                MappingRevisionModel.revision_digest,
            )
            .offset(offset)
            .limit(limit)
        )
        return [
            {"digest": row.revision_digest, "revision": row.document}
            for row in rows
        ]

    def _lock_sqlite_row(self, model, row_id: UUID) -> None:
        if self.session.get_bind().dialect.name == "sqlite":
            # SQLite ignores SELECT FOR UPDATE. Acquire the write lock before reading.
            table = model.__table__
            self.session.execute(
                update(table).where(table.c.id == row_id).values(id=table.c.id)
            )

    def _version(
        self, version_id: UUID, *, lock: bool = False,
    ) -> tuple[DatasetVersionModel, RawRecordManifest, dict]:
        if lock:
            self.session.flush()
            self._lock_sqlite_row(DatasetVersionModel, version_id)
        version = self.session.get(
            DatasetVersionModel, version_id, with_for_update=lock, populate_existing=lock,
        )
        if version is None:
            raise LookupError("DatasetVersion not found")
        refs = version.artifact_refs or {}
        manifest = refs.get("raw_record_manifest")
        if version.status != "PUBLISHED" or manifest is None:
            raise ValueError("mapping workflow requires a published v3 DatasetVersion")
        return version, RawRecordManifest.model_validate(manifest), refs.get("raw_file", {})

    def manifest(self, version_id: UUID) -> RawRecordManifest:
        return self._version(version_id)[1]

    def semantics(self, version_id: UUID) -> list[SemanticDefinition]:
        version, _, _ = self._version(version_id)
        dataset = self.session.get(DatasetModel, version.dataset_id)
        definitions = list(builtin_semantics())
        definitions.extend(
            SemanticDefinition.model_validate(item) for item in dataset.custom_semantics or []
        )
        return definitions

    def add_semantic(self, version_id: UUID, definition: SemanticDefinition) -> None:
        version, _, _ = self._version(version_id)
        self.session.flush()
        self._lock_sqlite_row(DatasetModel, version.dataset_id)
        dataset = self.session.get(
            DatasetModel, version.dataset_id, with_for_update=True, populate_existing=True,
        )
        if not definition.key.startswith("custom."):
            raise ValueError("custom semantic keys must start with custom.")
        if any(item["key"] == definition.key for item in dataset.custom_semantics or []):
            raise ValueError("semantic key already exists in this dataset")
        dataset.custom_semantics = [
            *(dataset.custom_semantics or []), definition.model_dump(mode="json")
        ]

    def recommend(self, version_id: UUID) -> dict:
        _, manifest, raw = self._version(version_id)
        fields = raw.get("fields", [])
        template = MappingTemplateResolver().resolve([field["name"] for field in fields])
        items = []
        field_labels = template.field_labels.copy() if template else {}
        if template is not None:
            candidates = TEMPLATE_SEMANTICS.get(template.template_id, {})
            definitions = {item.key: item for item in builtin_semantics()}
            for rule in template.mapping.mappings:
                target = rule.target.name if rule.target else rule.source_path.removeprefix("$.")
                if target not in candidates:
                    continue
                semantic = candidates[target]
                definition = definitions[semantic]
                field_labels.setdefault(rule.source_path.removeprefix("$."), definition.name)
                derived = semantic == "document.body.plain" and rule.source_path == "$.content"
                binding = FieldBinding(
                    semantic=semantic,
                    field_tokens=(rule.source_path.removeprefix("$."),),
                    representation=definition.representation,
                    source_representation="text/html" if derived else None,
                    transform="html_to_plain@1.0.0" if derived else None,
                )
                items.append(MappingDraftItem(
                    binding=binding, semantic_version=definition.version,
                    rationale=f"{template.template_id}@{template.template_version}: "
                    f"{rule.source_path} → {target}; pending human confirmation",
                ))
        return {
            "schema_fingerprint": manifest.schema_fingerprint,
            "fields": [
                {
                    **field,
                    "display_name": next(
                        (value.strip() for value in (
                            field.get("display_name"), field.get("comment"), field.get("description"),
                            field_labels.get(field["name"]),
                        ) if isinstance(value, str) and value.strip()),
                        None,
                    ),
                }
                for field in fields
            ],
            "provenance": template.provenance() if template else None,
            "items": items,
        }

    def draft(self, version_id: UUID) -> dict:
        version, manifest, _ = self._version(version_id)
        return {
            "schema_fingerprint": manifest.schema_fingerprint,
            "base_digest": (version.mapping_draft or {}).get("base_digest"),
            "items": (version.mapping_draft or {}).get("items", []),
            "default_digest": version.default_mapping_digest,
        }

    def _validated_binding(
        self, item: MappingDraftItem, definitions: list[SemanticDefinition],
        fields: set[str], resolver: InputResolver,
    ) -> FieldBinding:
        binding = item.binding
        choices = [
            definition for definition in definitions
            if definition.key == binding.semantic
            and definition.representation == binding.representation
            and (item.semantic_version is None or definition.version == item.semantic_version)
        ]
        if len(choices) != 1:
            raise ValueError(f"unknown or ambiguous semantic: {binding.semantic}")
        if binding.field_tokens[0] not in fields or any(not token for token in binding.field_tokens):
            raise ValueError("source path must start with a field in the published Schema")
        transform = resolver.transforms.get(binding.transform or "")
        if binding.transform is not None and (
            transform is None or transform.input_representation != binding.source_representation
            or transform.output_representation != binding.representation
        ):
            raise ValueError("unknown or incompatible transform")
        if binding.transform is None and binding.source_representation not in (
            None, binding.representation,
        ):
            raise ValueError("changing representation requires a registered transform")
        return binding.model_copy(update={
            "definition": choices[0],
            "transform_digest": transform.implementation_digest if transform else None,
        })

    def save_draft(
        self, version_id: UUID, items: list[MappingDraftItem], resolver: InputResolver,
        *, from_digest: str | None = None, confirm_all: bool = False,
    ) -> dict:
        version, manifest, raw = self._version(version_id, lock=True)
        current = self.draft(version_id)
        if from_digest is not None:
            historical = self.get(version_id, from_digest)
            if historical is None:
                raise LookupError("Mapping revision not found")
            if confirm_all:
                raise ValueError("from_digest cannot be combined with confirm_all")
            if items:
                raise ValueError("from_digest cannot be combined with items")
            items = [
                MappingDraftItem(
                    binding=binding,
                    semantic_version=binding.definition.version if binding.definition else None,
                ) for binding in historical.bindings
            ]
        definitions = self.semantics(version_id)
        fields = {field["name"] for field in raw.get("fields", [])}
        previous_items = [MappingDraftItem.model_validate(value) for value in current["items"]]
        previous = {
            (entry.binding.semantic, entry.binding.representation): entry
            for entry in previous_items
        }
        saved = []
        for item in items:
            binding = self._validated_binding(item, definitions, fields, resolver)
            old = previous.get((binding.semantic, binding.representation))
            confirmed = confirm_all or bool(
                from_digest is None and old and old.confirmed and old.binding == binding
            )
            saved.append(item.model_copy(update={
                "binding": binding,
                "semantic_version": binding.definition.version,
                "confirmed": confirmed,
            }))
        MappingRevision(
            dataset_version_id=version_id, schema_fingerprint=manifest.schema_fingerprint,
            bindings=tuple(item.binding for item in saved),
        )
        version.mapping_draft = {
            "base_digest": from_digest if from_digest is not None else current["base_digest"],
            "items": [item.model_dump(mode="json") for item in saved],
        }
        return self.draft(version_id)

    def set_confirmation(
        self, version_id: UUID, semantic: str, representation: Representation, confirmed: bool,
    ) -> dict:
        version, _, _ = self._version(version_id, lock=True)
        draft = self.draft(version_id)
        for index, item in enumerate(draft["items"]):
            binding = item["binding"]
            if (binding["semantic"], binding["representation"]) == (semantic, representation):
                items = list(draft["items"])
                items[index] = {**item, "confirmed": confirmed}
                version.mapping_draft = {"base_digest": draft["base_digest"], "items": items}
                return self.draft(version_id)
        raise LookupError("Mapping draft binding not found")

    def publish_draft(
        self, version_id: UUID, resolver: InputResolver, *, make_default: bool,
    ) -> dict:
        version, manifest, _ = self._version(version_id, lock=True)
        bindings = tuple(
            MappingDraftItem.model_validate(item).binding
            for item in self.draft(version_id)["items"] if item["confirmed"]
        )
        if not bindings:
            raise ValueError("mapping draft has no confirmed bindings")
        for binding in bindings:
            if binding.transform is not None:
                transform = resolver.transforms.get(binding.transform)
                if transform is None or transform.implementation_digest != binding.transform_digest:
                    raise ValueError("draft transform changed; save and confirm the binding again")
        revision = MappingRevision(
            dataset_version_id=version_id, schema_fingerprint=manifest.schema_fingerprint,
            bindings=bindings,
        )
        digest = self.confirm(revision)
        if make_default:
            version.default_mapping_digest = digest
        return {"digest": digest, "revision": revision, "default_digest": version.default_mapping_digest}

    def set_default(self, version_id: UUID, digest: str | None) -> dict:
        version, _, _ = self._version(version_id, lock=True)
        if digest is not None and self.get(version_id, digest) is None:
            raise LookupError("Mapping revision not found")
        version.default_mapping_digest = digest
        return {"default_digest": digest}

    def preview(
        self, version_id: UUID, record: RawSampleRecord, resolver: InputResolver,
        digest: str | None, slots: tuple[InputSlot, ...],
    ) -> dict:
        _, manifest, _ = self._version(version_id)
        if digest is not None:
            revision = self.get(version_id, digest)
            if revision is None:
                raise LookupError("Mapping revision not found")
        else:
            revision = MappingRevision(
                dataset_version_id=version_id, schema_fingerprint=manifest.schema_fingerprint,
                bindings=tuple(
                    MappingDraftItem.model_validate(item).binding
                    for item in self.draft(version_id)["items"]
                ),
            )
        bindings = {(b.semantic, b.representation): b for b in revision.bindings}
        if not slots:
            definitions = {(d.key, d.representation): d for d in self.semantics(version_id)}
            slots = tuple(
                InputSlot(
                    name=b.semantic + ":" + b.representation, semantic=b.semantic,
                    representation=b.representation,
                    value_type=definition.type if definition else "any",
                    cardinality=definition.cardinality if definition else "one",
                    required=False,
                )
                for b in revision.bindings
                for definition in [b.definition or definitions.get((b.semantic, b.representation))]
            )
        contract = CalculatorInputContract(
            version="1.0", grain="RECORD", mode="DIRECT",
            slots=tuple(slot.model_copy(update={"required": False}) for slot in slots),
        )
        plan = resolver.preflight(manifest, revision, contract)
        resolved = next(resolver.iter_records(plan, [record]))
        values = {}
        for slot in slots:
            entry = resolved.values[slot.name]
            binding = bindings.get((slot.semantic, slot.representation))
            raw_value = record.data
            for token in binding.field_tokens if binding else ():
                raw_value = raw_value.get(token) if isinstance(raw_value, dict) else None
            values[slot.name] = {
                **asdict(entry),
                "semantic": slot.semantic,
                "representation": slot.representation,
                "source_representation": (
                    binding.source_representation or binding.representation if binding else None
                ),
                "field_tokens": binding.field_tokens if binding else None,
                "raw_value": raw_value if binding else None,
                "diagnostics": [entry.status] if entry.status != "PRESENT" else [],
            }
        return {"digest": digest, "sample_id": record.id, "values": values}
