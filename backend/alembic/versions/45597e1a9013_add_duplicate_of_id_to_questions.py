"""add duplicate_of_id to questions

Revision ID: 45597e1a9013
Revises: c912b07a1eb6
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45597e1a9013'
down_revision: Union[str, Sequence[str], None] = 'c912b07a1eb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('duplicate_of_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_column('duplicate_of_id')
