from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_property_search_index"
down_revision = "0002_traffic_v2_network_signals"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def upgrade() -> None:
    tables = _table_names()
    if "public_api_property_index" in tables:
        columns = {
            str(column["name"])
            for column in sa.inspect(op.get_bind()).get_columns(
                "public_api_property_index"
            )
        }
        if "aliases" not in columns:
            op.add_column(
                "public_api_property_index",
                sa.Column(
                    "aliases",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )
        if "search_text_normalized" not in columns:
            op.add_column(
                "public_api_property_index",
                sa.Column(
                    "search_text_normalized",
                    sa.Text(),
                    nullable=False,
                    server_default="",
                ),
            )
        if "idx_public_api_property_index_search" not in _index_names(
            "public_api_property_index"
        ):
            op.create_index(
                "idx_public_api_property_index_search",
                "public_api_property_index",
                ["search_text_normalized"],
            )

    if "property_aliases" in tables and "idx_property_aliases_property" not in _index_names(
        "property_aliases"
    ):
        op.create_index(
            "idx_property_aliases_property",
            "property_aliases",
            ["property_id"],
        )


def downgrade() -> None:
    tables = _table_names()
    if "property_aliases" in tables and "idx_property_aliases_property" in _index_names(
        "property_aliases"
    ):
        op.drop_index("idx_property_aliases_property", table_name="property_aliases")

    if "public_api_property_index" not in tables:
        return
    if "idx_public_api_property_index_search" in _index_names(
        "public_api_property_index"
    ):
        op.drop_index(
            "idx_public_api_property_index_search",
            table_name="public_api_property_index",
        )
    columns = {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(
            "public_api_property_index"
        )
    }
    with op.batch_alter_table("public_api_property_index") as batch_op:
        if "search_text_normalized" in columns:
            batch_op.drop_column("search_text_normalized")
        if "aliases" in columns:
            batch_op.drop_column("aliases")
