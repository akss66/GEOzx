"""Enforce fail-closed WeChat article documents at the database boundary.

Revision ID: 20260811_0320
Revises: 20260811_0310
Create Date: 2026-08-11 04:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_0320"
down_revision: str | None = "20260811_0310"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION wechat_article_document_is_valid(document jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        AS $$
        DECLARE
            block jsonb;
            item jsonb;
            block_id text;
            block_ids text[] := ARRAY[]::text[];
            block_type text;
            text_value text;
        BEGIN
            IF jsonb_typeof(document) <> 'object'
                OR NOT (document ? 'title' AND document ? 'digest' AND document ? 'blocks')
                OR (document - 'title' - 'digest' - 'author' - 'blocks') <> '{}'::jsonb
                OR jsonb_typeof(document->'title') <> 'string'
                OR jsonb_typeof(document->'digest') <> 'string'
                OR (document ? 'author' AND document->'author' <> 'null'::jsonb
                    AND jsonb_typeof(document->'author') <> 'string')
                OR jsonb_typeof(document->'blocks') <> 'array'
                OR jsonb_array_length(document->'blocks') NOT BETWEEN 1 AND 500
            THEN
                RETURN false;
            END IF;

            IF char_length(document->>'title') NOT BETWEEN 1 AND 64
                OR char_length(document->>'digest') NOT BETWEEN 1 AND 120
                OR (document ? 'author' AND document->'author' <> 'null'::jsonb
                    AND char_length(document->>'author') NOT BETWEEN 1 AND 120)
                OR document->>'title' LIKE '%<%' OR document->>'title' LIKE '%>%'
                OR document->>'digest' LIKE '%<%' OR document->>'digest' LIKE '%>%'
                OR (document ? 'author' AND document->'author' <> 'null'::jsonb
                    AND (document->>'author' LIKE '%<%' OR document->>'author' LIKE '%>%'))
            THEN
                RETURN false;
            END IF;

            FOR block IN SELECT value FROM jsonb_array_elements(document->'blocks')
            LOOP
                IF jsonb_typeof(block) <> 'object'
                    OR NOT (block ? 'type' AND block ? 'block_id')
                    OR jsonb_typeof(block->'type') <> 'string'
                    OR jsonb_typeof(block->'block_id') <> 'string'
                THEN
                    RETURN false;
                END IF;

                block_id := block->>'block_id';
                block_type := block->>'type';
                IF block_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
                    OR block_id = ANY(block_ids)
                THEN
                    RETURN false;
                END IF;
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
                            OR (block ? 'attribution'
                                AND block->'attribution' <> 'null'::jsonb AND (
                                jsonb_typeof(block->'attribution') <> 'string'
                                OR char_length(block->>'attribution') NOT BETWEEN 1 AND 300
                            ))
                        THEN RETURN false; END IF;
                        text_value := coalesce(block->>'text', '')
                            || coalesce(block->>'attribution', '');
                    WHEN 'list' THEN
                        IF (block - 'type' - 'block_id' - 'style' - 'items') <> '{}'::jsonb
                            OR jsonb_typeof(block->'style') <> 'string'
                            OR (block->>'style') NOT IN ('ordered', 'unordered')
                            OR jsonb_typeof(block->'items') <> 'array'
                            OR jsonb_array_length(block->'items') NOT BETWEEN 1 AND 30
                        THEN RETURN false; END IF;
                        FOR item IN SELECT value FROM jsonb_array_elements(block->'items')
                        LOOP
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
                                'consult', 'contact', 'visit', 'register', 'learn_more'
                            )
                            OR jsonb_typeof(block->'url') <> 'string'
                            OR block->>'url' !~ '^https?://.+'
                        THEN RETURN false; END IF;
                        text_value := block->>'label';
                    ELSE
                        RETURN false;
                END CASE;

                IF text_value LIKE '%<%' OR text_value LIKE '%>%' THEN RETURN false; END IF;
            END LOOP;

            RETURN true;
        END;
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM article_working_copies
                WHERE NOT wechat_article_document_is_valid(document)
            ) THEN
                RAISE EXCEPTION
                    'cannot enforce WeChat article document contract with invalid working copies';
            END IF;
        END $$;
        """
    )
    op.create_check_constraint(
        "ck_article_working_copy_document_valid",
        "article_working_copies",
        "wechat_article_document_is_valid(document)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_article_working_copy_document_valid",
        "article_working_copies",
        type_="check",
    )
    op.execute("DROP FUNCTION wechat_article_document_is_valid(jsonb)")
