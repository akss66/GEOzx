"""Immutable, integrity-checked prompt registry."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Literal


class PromptRegistryError(RuntimeError):
    """Base error for production prompt loading."""


class PromptNotFound(PromptRegistryError):
    pass


class PromptNotPublishable(PromptRegistryError):
    pass


class PromptIntegrityError(PromptRegistryError):
    pass


class PromptTemplateError(PromptRegistryError):
    pass


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    relative_path: str
    content_hash: str
    schema_version: str
    status: Literal["active", "draft", "retired"] = "active"


@dataclass(frozen=True)
class LoadedPrompt:
    spec: PromptSpec
    content: str
    content_hash: str


class PromptRegistry:
    def __init__(self, root: Path, specs: list[PromptSpec]) -> None:
        self._root = root.resolve()
        self._specs: dict[tuple[str, str], PromptSpec] = {}
        for spec in specs:
            key = (spec.id, spec.version)
            if key in self._specs:
                raise ValueError(f"duplicate prompt version: {spec.id}@{spec.version}")
            self._specs[key] = spec

    @classmethod
    def production(cls) -> PromptRegistry:
        return cls(Path(__file__).resolve().parent, list(PRODUCTION_PROMPTS))

    def prompt_ids(self) -> list[str]:
        return sorted({prompt_id for prompt_id, _version in self._specs})

    def load(self, prompt_id: str, version: str | None = None) -> LoadedPrompt:
        spec = self._resolve_spec(prompt_id, version)
        if spec.status != "active":
            raise PromptNotPublishable(
                f"prompt is not active: {spec.id}@{spec.version} ({spec.status})"
            )
        path = (self._root / spec.relative_path).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise PromptIntegrityError("prompt path escapes registry root") from exc
        if not path.is_file():
            raise PromptNotFound(f"prompt file is missing: {spec.relative_path}")
        content = path.read_text(encoding="utf-8")
        content_hash = sha256(content.encode("utf-8")).hexdigest()
        if content_hash != spec.content_hash:
            raise PromptIntegrityError(
                f"prompt hash mismatch: {spec.id}@{spec.version}"
            )
        return LoadedPrompt(spec=spec, content=content.strip(), content_hash=content_hash)

    def render(
        self,
        prompt_id: str,
        *,
        variables: dict[str, str],
        version: str | None = None,
    ) -> LoadedPrompt:
        loaded = self.load(prompt_id, version)
        template = Template(loaded.content)
        expected = set(template.get_identifiers())
        supplied = set(variables)
        missing = expected - supplied
        unknown = supplied - expected
        if missing or unknown:
            raise PromptTemplateError(
                f"prompt variables mismatch: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        try:
            content = template.substitute(variables)
        except (KeyError, ValueError) as exc:
            raise PromptTemplateError("prompt template could not be rendered") from exc
        return LoadedPrompt(
            spec=loaded.spec,
            content=content,
            content_hash=loaded.content_hash,
        )

    def _resolve_spec(self, prompt_id: str, version: str | None) -> PromptSpec:
        if version is not None:
            spec = self._specs.get((prompt_id, version))
            if spec is None:
                raise PromptNotFound(f"unknown prompt: {prompt_id}@{version}")
            return spec
        active = [
            spec
            for (candidate_id, _candidate_version), spec in self._specs.items()
            if candidate_id == prompt_id and spec.status == "active"
        ]
        if len(active) != 1:
            raise PromptNotFound(
                f"prompt must have exactly one active version: {prompt_id}"
            )
        return active[0]


# Hashes are pinned to the reviewed file bytes. Updating a prompt requires a new
# version or an explicit manifest hash update in the same reviewed change.
PRODUCTION_PROMPTS: tuple[PromptSpec, ...] = (
    PromptSpec(
        "main-agent.intent",
        "1.0.0",
        "main-agent/intent/v1.md",
        "aaba779fa452fac1087fd1522fb488c726777280756595040013a35958d6e444",
        "intent-decision/v1",
        status="retired",
    ),
    PromptSpec(
        "main-agent.intent",
        "2.0.0",
        "main-agent/intent/v2.md",
        "c1782851e01eb4357ba14879fe220a77de3044a413e82b1f6f8b3eba83758035",
        "turn-route-decision/v1",
    ),
    PromptSpec(
        "main-agent.next-step",
        "1.0.0",
        "main-agent/next-step/v1.md",
        "0db39e1ad91ebdcdff473a0c96479df79eb349555141d9243475245885f6f091",
        "runtime-next-step/v1",
    ),
    PromptSpec(
        "main-agent.strategy-planning",
        "1.0.0",
        "main-agent/strategy-planning/v1.md",
        "0c82d2f5e2bb825112bf6524b23fa2c093d2812c858e0cc12e0e761685862041",
        "operating-strategy/v1",
    ),
    PromptSpec(
        "main-agent.decision-revision",
        "1.0.0",
        "main-agent/decision-revision/v1.md",
        "a0789a6c51825debe17d847f8956b6a81fe0f4cc354614b14aeb103b6cf80004",
        "decision-request/v1",
    ),
    PromptSpec(
        "main-agent.critic",
        "1.0.0",
        "main-agent/critic/v1.md",
        "cbd9d1e469c2961e3939d9b5616dcc4f60912498c7ef2eaccedfaa6e248d2900",
        "critic-evaluation/v1",
    ),
    PromptSpec(
        "main-agent.acknowledgement",
        "1.0.0",
        "main-agent/acknowledgement/v1.md",
        "2a8ef409cff4a387da0f0b46f4cdb0af0385c37b591f4664997a7ad8b4f5bdcd",
        "natural-language/v1",
    ),
    PromptSpec(
        "main-agent.summary",
        "1.0.0",
        "main-agent/summary/v1.md",
        "a4bfbb8377bd943ea4ab987d918b31c437e23c9f87a9a6e40093a0f25f74216f",
        "natural-language/v1",
    ),
    PromptSpec(
        "main-agent.conversation",
        "1.0.0",
        "main-agent/conversation/v1.md",
        "455727f71dd7e1a1de91a6268d4a8a683e12240ef99036448d27934e85c4370e",
        "natural-language/v1",
    ),
    PromptSpec(
        "expert.01-positioning",
        "1.0.0",
        "experts/01-positioning/v1.md",
        "9903020ab775fa6999943b83ca085587265ff97f3ceae31118750cc4e5bc45f7",
        "positioning-strategy/v1",
    ),
    PromptSpec(
        "expert.02-content",
        "1.0.0",
        "experts/02-content/v1.md",
        "c6f7daf10613d576afd3afc4f1addc75181a2800c3cf25027600f97858ca8b60",
        "video-script/v1",
    ),
    PromptSpec(
        "expert.03-art",
        "1.0.0",
        "experts/03-art/v1.md",
        "88efa28bca4a6bb863542028bbafbce3721cce573eb69e37e07a06105fc72ce3",
        "art-prompt/v1",
    ),
    PromptSpec(
        "expert.04-video",
        "1.0.0",
        "experts/04-video/v1.md",
        "891644e91d2c17319e0a6b6f1827dba9460d9bb67d666cabd638b3ea85f0be28",
        "video-asset/v1",
    ),
    PromptSpec(
        "expert.05-editing",
        "1.0.0",
        "experts/05-editing/v1.md",
        "042935f7f2caae33ae9559a8146c9e4fedb771e3cde4b1adebbb12406ce1d173",
        "edited-video/v1",
    ),
    PromptSpec(
        "expert.06-operation",
        "1.0.0",
        "experts/06-operation/v1.md",
        "9d9b7235eb8067dde0b82b4e333d8df1e2ce0de25d6535c4de352082b48a12c3",
        "review-report/v1",
    ),
    PromptSpec(
        "expert.07-advertiser",
        "1.0.0",
        "experts/07-advertiser/v1.md",
        "552c11a79ec8a365ac2f8b1e7141d7bf2e5e37db9f886175f36a35c0c286f5c0",
        "ad-plan/v1",
    ),
    PromptSpec(
        "expert.08-customer-service",
        "1.0.0",
        "experts/08-customer-service/v1.md",
        "a4c0dc615eb93167b1e4c6d42c4061aafeb564d6dff00a5a19d463258a9f4b22",
        "customer-service-record/v1",
    ),
    PromptSpec(
        "memory.compactor",
        "1.0.0",
        "memory-compactor/v1.md",
        "f956eea8fb026828861aca2072ac1e96381294b7edb270ef27402449703c3443",
        "runtime-memory-summary/v1",
    ),
    PromptSpec(
        "knowledge.extractor",
        "1.0.0",
        "knowledge-extractor/v1.md",
        "2724b6400c7cf7451f08127bb3fadf3ca10180e99ffbd2cd2e716d9c71002906",
        "knowledge-suggestions/v1",
    ),
)

prompt_registry = PromptRegistry.production()
