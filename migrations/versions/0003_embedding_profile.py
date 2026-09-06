"""Persist dataset-bound encrypted embedding configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0003_embedding_profile"
down_revision = "0002_delete_dataset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("encrypted_embedding_profile", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "encrypted_embedding_profile")
