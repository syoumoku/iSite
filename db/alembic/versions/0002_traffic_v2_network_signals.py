from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from isite2.db.models import (
    ComplaintObservationDB,
    NetworkPerformanceObservationDB,
    PropertyComplaintRollupDB,
    PropertyNetworkPerformanceRollupDB,
    TrafficEstimateV2DB,
)

revision = "0002_traffic_v2_network_signals"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


_NEW_TABLES = (
    TrafficEstimateV2DB.__table__,
    ComplaintObservationDB.__table__,
    PropertyComplaintRollupDB.__table__,
    NetworkPerformanceObservationDB.__table__,
    PropertyNetworkPerformanceRollupDB.__table__,
)

_PUBLIC_INDEX_COLUMNS = (
    sa.Column("annual_visits_p10", sa.Float(), nullable=True),
    sa.Column("annual_visits_p50", sa.Float(), nullable=True),
    sa.Column("annual_visits_p90", sa.Float(), nullable=True),
    sa.Column("traffic_model_version", sa.String(length=64), nullable=True),
    sa.Column("complaint_pressure", sa.String(length=32), nullable=True),
    sa.Column("network_validation_priority", sa.String(length=32), nullable=True),
    sa.Column("network_data_freshness", sa.String(length=32), nullable=True),
    sa.Column("feature_flags", sa.JSON(), nullable=True),
)

_TRAFFIC_V2_COLUMNS = (
    sa.Column(
        "activation_status",
        sa.String(length=64),
        nullable=False,
        server_default="not_evaluated",
    ),
    sa.Column("activation_reason", sa.Text(), nullable=True),
    sa.Column("v1_annual_visits_est", sa.Float(), nullable=True),
    sa.Column("v1_annual_visits_raw", sa.Float(), nullable=True),
    sa.Column(
        "v1_demand_snapshot",
        sa.JSON(),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names()
    for table in _NEW_TABLES:
        if table.name not in tables:
            table.create(bind=bind, checkfirst=True)

    traffic_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("traffic_estimates_v2")
    }
    for column in _TRAFFIC_V2_COLUMNS:
        if column.name not in traffic_columns:
            op.add_column("traffic_estimates_v2", column.copy())

    if "public_api_property_index" not in tables:
        return
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("public_api_property_index")
    }
    for column in _PUBLIC_INDEX_COLUMNS:
        if column.name not in existing_columns:
            op.add_column("public_api_property_index", column.copy())


def downgrade() -> None:
    tables = _table_names()
    if "public_api_property_index" in tables:
        existing_columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns(
                "public_api_property_index"
            )
        }
        with op.batch_alter_table("public_api_property_index") as batch_op:
            for column in reversed(_PUBLIC_INDEX_COLUMNS):
                if column.name in existing_columns:
                    batch_op.drop_column(column.name)

    for table in reversed(_NEW_TABLES):
        if table.name in tables:
            op.drop_table(table.name)
