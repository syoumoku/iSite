from __future__ import annotations

from alembic import op

from isite2.db.models import NetworkPerformanceTileDB


revision = "0004_network_performance_tiles"
down_revision = "0003_property_search_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    NetworkPerformanceTileDB.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    op.drop_table(NetworkPerformanceTileDB.__tablename__)
