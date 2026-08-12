"""Add immutable WeChat article claim evidence lineage.

Revision ID: 20260811_0330
Revises: 20260811_0320
Create Date: 2026-08-12 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0330"
down_revision: str | None = "20260811_0320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VALIDATOR_WITH_CLAIMS = r"""
CREATE OR REPLACE FUNCTION wechat_article_document_is_valid(document jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    block jsonb;
    item jsonb;
    claim jsonb;
    citation jsonb;
    block_id text;
    block_ids text[] := ARRAY[]::text[];
    claim_id text;
    claim_ids text[] := ARRAY[]::text[];
    citation_ids bigint[];
    block_type text;
    text_value text;
BEGIN
    IF jsonb_typeof(document) <> 'object'
        OR NOT (document ? 'title' AND document ? 'digest' AND document ? 'blocks')
        OR (document - 'title' - 'digest' - 'author' - 'blocks' - 'claims') <> '{}'::jsonb
        OR jsonb_typeof(document->'title') <> 'string'
        OR jsonb_typeof(document->'digest') <> 'string'
        OR (document ? 'author' AND document->'author' <> 'null'::jsonb
            AND jsonb_typeof(document->'author') <> 'string')
        OR jsonb_typeof(document->'blocks') <> 'array'
        OR jsonb_array_length(document->'blocks') NOT BETWEEN 1 AND 500
        OR (document ? 'claims' AND (
            jsonb_typeof(document->'claims') <> 'array'
            OR jsonb_array_length(document->'claims') > 200
        ))
    THEN RETURN false; END IF;

    IF char_length(document->>'title') NOT BETWEEN 1 AND 64
        OR char_length(document->>'digest') NOT BETWEEN 1 AND 120
        OR (document ? 'author' AND document->'author' <> 'null'::jsonb
            AND char_length(document->>'author') NOT BETWEEN 1 AND 120)
        OR document->>'title' LIKE '%<%' OR document->>'title' LIKE '%>%'
        OR document->>'digest' LIKE '%<%' OR document->>'digest' LIKE '%>%'
        OR (document ? 'author' AND document->'author' <> 'null'::jsonb
            AND (document->>'author' LIKE '%<%' OR document->>'author' LIKE '%>%'))
    THEN RETURN false; END IF;

    FOR block IN SELECT value FROM jsonb_array_elements(document->'blocks') LOOP
        IF jsonb_typeof(block) <> 'object'
            OR NOT (block ? 'type' AND block ? 'block_id')
            OR jsonb_typeof(block->'type') <> 'string'
            OR jsonb_typeof(block->'block_id') <> 'string'
        THEN RETURN false; END IF;
        block_id := block->>'block_id';
        block_type := block->>'type';
        IF block_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$' OR block_id = ANY(block_ids)
        THEN RETURN false; END IF;
        block_ids := array_append(block_ids, block_id);

        CASE block_type
            WHEN 'heading' THEN
                IF (block - 'type' - 'block_id' - 'level' - 'text') <> '{}'::jsonb
                    OR jsonb_typeof(block->'level') <> 'number'
                    OR (block->>'level') NOT IN ('2', '3', '4')
                    OR jsonb_typeof(block->'text') <> 'string'
                    OR char_length(block->>'text') NOT BETWEEN 1 AND 500
                THEN RETURN false; END IF;
                text_value := block->>'text';
            WHEN 'paragraph' THEN
                IF (block - 'type' - 'block_id' - 'text') <> '{}'::jsonb
                    OR jsonb_typeof(block->'text') <> 'string'
                    OR char_length(block->>'text') NOT BETWEEN 1 AND 20000
                THEN RETURN false; END IF;
                text_value := block->>'text';
            WHEN 'quote' THEN
                IF (block - 'type' - 'block_id' - 'text' - 'attribution') <> '{}'::jsonb
                    OR jsonb_typeof(block->'text') <> 'string'
                    OR char_length(block->>'text') NOT BETWEEN 1 AND 4000
                    OR (block ? 'attribution' AND block->'attribution' <> 'null'::jsonb AND (
                        jsonb_typeof(block->'attribution') <> 'string'
                        OR char_length(block->>'attribution') NOT BETWEEN 1 AND 300))
                THEN RETURN false; END IF;
                text_value := coalesce(block->>'text', '') || coalesce(block->>'attribution', '');
            WHEN 'list' THEN
                IF (block - 'type' - 'block_id' - 'style' - 'items') <> '{}'::jsonb
                    OR jsonb_typeof(block->'style') <> 'string'
                    OR (block->>'style') NOT IN ('ordered', 'unordered')
                    OR jsonb_typeof(block->'items') <> 'array'
                    OR jsonb_array_length(block->'items') NOT BETWEEN 1 AND 30
                THEN RETURN false; END IF;
                FOR item IN SELECT value FROM jsonb_array_elements(block->'items') LOOP
                    IF jsonb_typeof(item) <> 'string'
                        OR char_length(item #>> '{}') NOT BETWEEN 1 AND 2000
                        OR item #>> '{}' LIKE '%<%' OR item #>> '{}' LIKE '%>%'
                    THEN RETURN false; END IF;
                END LOOP;
                text_value := '';
            WHEN 'callout' THEN
                IF (block - 'type' - 'block_id' - 'tone' - 'text') <> '{}'::jsonb
                    OR jsonb_typeof(block->'tone') <> 'string'
                    OR (block->>'tone') NOT IN ('info', 'tip', 'warning')
                    OR jsonb_typeof(block->'text') <> 'string'
                    OR char_length(block->>'text') NOT BETWEEN 1 AND 4000
                THEN RETURN false; END IF;
                text_value := block->>'text';
            WHEN 'imageSlot' THEN
                IF (block - 'type' - 'block_id' - 'slot_key') <> '{}'::jsonb
                    OR jsonb_typeof(block->'slot_key') <> 'string'
                    OR block->>'slot_key' !~ '^[a-z][a-z0-9_-]{0,127}$'
                THEN RETURN false; END IF;
                text_value := '';
            WHEN 'divider' THEN
                IF (block - 'type' - 'block_id') <> '{}'::jsonb THEN RETURN false; END IF;
                text_value := '';
            WHEN 'cta' THEN
                IF (block - 'type' - 'block_id' - 'label' - 'action' - 'url') <> '{}'::jsonb
                    OR jsonb_typeof(block->'label') <> 'string'
                    OR char_length(block->>'label') NOT BETWEEN 1 AND 120
                    OR jsonb_typeof(block->'action') <> 'string'
                    OR (block->>'action') NOT IN (
                        'consult', 'contact', 'visit', 'register', 'learn_more')
                    OR jsonb_typeof(block->'url') <> 'string'
                    OR block->>'url' !~ '^https?://.+'
                THEN RETURN false; END IF;
                text_value := block->>'label';
            ELSE RETURN false;
        END CASE;
        IF text_value LIKE '%<%' OR text_value LIKE '%>%' THEN RETURN false; END IF;
    END LOOP;

    IF document ? 'claims' THEN
        FOR claim IN SELECT value FROM jsonb_array_elements(document->'claims') LOOP
            IF jsonb_typeof(claim) <> 'object'
                OR (claim - 'claim_id' - 'block_id' - 'kind' - 'text' - 'citation_ids')
                    <> '{}'::jsonb
                OR NOT (claim ? 'claim_id' AND claim ? 'block_id' AND claim ? 'kind'
                    AND claim ? 'text' AND claim ? 'citation_ids')
                OR jsonb_typeof(claim->'claim_id') <> 'string'
                OR jsonb_typeof(claim->'block_id') <> 'string'
                OR jsonb_typeof(claim->'kind') <> 'string'
                OR jsonb_typeof(claim->'text') <> 'string'
                OR jsonb_typeof(claim->'citation_ids') <> 'array'
                OR jsonb_array_length(claim->'citation_ids') > 20
            THEN RETURN false; END IF;
            claim_id := claim->>'claim_id';
            IF claim_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
                OR claim_id = ANY(claim_ids)
                OR (claim->>'block_id') <> ALL(block_ids)
                OR (claim->>'kind') NOT IN (
                    'product_fact', 'case', 'promise', 'price', 'numeric', 'public_info')
                OR char_length(claim->>'text') NOT BETWEEN 1 AND 20000
                OR claim->>'text' LIKE '%<%' OR claim->>'text' LIKE '%>%'
            THEN RETURN false; END IF;
            claim_ids := array_append(claim_ids, claim_id);
            citation_ids := ARRAY[]::bigint[];
            FOR citation IN SELECT value FROM jsonb_array_elements(claim->'citation_ids') LOOP
                IF jsonb_typeof(citation) <> 'number'
                    OR (citation #>> '{}') !~ '^[1-9][0-9]*$'
                    OR (citation #>> '{}')::bigint = ANY(citation_ids)
                THEN RETURN false; END IF;
                citation_ids := array_append(citation_ids, (citation #>> '{}')::bigint);
            END LOOP;
        END LOOP;
    END IF;
    RETURN true;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "knowledge_citations", sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "article_version_citations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("deliverable_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_citation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["deliverable_id"], ["deliverables.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["knowledge_citation_id"], ["knowledge_citations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deliverable_id",
            "knowledge_citation_id",
            name="uq_article_version_citation_evidence",
        ),
    )
    op.create_index(
        op.f("ix_article_version_citations_deliverable_id"),
        "article_version_citations",
        ["deliverable_id"],
    )
    op.create_index(
        op.f("ix_article_version_citations_knowledge_citation_id"),
        "article_version_citations",
        ["knowledge_citation_id"],
    )
    op.execute(_VALIDATOR_WITH_CLAIMS)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM article_version_citations) THEN
                RAISE EXCEPTION 'cannot downgrade while article version citations exist';
            END IF;
            IF EXISTS (SELECT 1 FROM article_working_copies WHERE document ? 'claims') THEN
                RAISE EXCEPTION 'cannot downgrade while article working copies contain claims';
            END IF;
        END $$;
        """
    )
    op.drop_index(
        op.f("ix_article_version_citations_knowledge_citation_id"),
        table_name="article_version_citations",
    )
    op.drop_index(
        op.f("ix_article_version_citations_deliverable_id"),
        table_name="article_version_citations",
    )
    op.drop_table("article_version_citations")
    op.drop_column("knowledge_citations", "expires_at")
    op.drop_column("knowledge_citations", "effective_at")
    # Restore 0320 behavior: old documents remain valid but direct SQL cannot add claims after
    # downgrade. The remaining validator body is deliberately identical to the upgraded body.
    op.execute(
        _VALIDATOR_WITH_CLAIMS.replace(
            "BEGIN\n    IF jsonb_typeof(document)",
            "BEGIN\n    IF document ? 'claims' THEN RETURN false; END IF;\n"
            "    IF jsonb_typeof(document)",
        )
    )
