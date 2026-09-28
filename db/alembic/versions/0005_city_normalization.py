from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from isite2.db.models import (
    CityCanonicalUnitDB,
    CityLocalityMappingDB,
    PropertyCityAssignmentDB,
)


revision = "0005_city_normalization"
down_revision = "0004_network_performance_tiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in (
        CityCanonicalUnitDB.__table__,
        CityLocalityMappingDB.__table__,
        PropertyCityAssignmentDB.__table__,
    ):
        table.create(bind=bind, checkfirst=True)

    for table_name in (
        "properties",
        "public_api_property_packets",
        "public_api_map_features",
        "public_api_review_queue_rows",
    ):
        if table_name not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "city_id" not in columns:
            op.add_column(table_name, sa.Column("city_id", sa.String(length=128)))

    if "public_api_property_index" in tables:
        columns = {
            column["name"]
            for column in inspector.get_columns("public_api_property_index")
        }
        if "city_id" not in columns:
            op.add_column(
                "public_api_property_index",
                sa.Column("city_id", sa.String(length=128)),
            )
        if "city_assignment" not in columns:
            op.add_column(
                "public_api_property_index",
                sa.Column("city_assignment", sa.JSON()),
            )

    op.create_index(
        "idx_properties_country_city_id",
        "properties",
        ["country", "city_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_properties_country_city_id", table_name="properties", if_exists=True)
    for table_name in (
        "public_api_review_queue_rows",
        "public_api_map_features",
        "public_api_property_packets",
    ):
        op.drop_column(table_name, "city_id")
    op.drop_column("public_api_property_index", "city_assignment")
    op.drop_column("public_api_property_index", "city_id")
    op.drop_column("properties", "city_id")
    op.drop_table(PropertyCityAssignmentDB.__tablename__)
    op.drop_table(CityLocalityMappingDB.__tablename__)
    op.drop_table(CityCanonicalUnitDB.__tablename__)
