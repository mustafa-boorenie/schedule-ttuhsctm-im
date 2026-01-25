"""Add attending_name to call_assignments

Revision ID: 001
Revises:
Create Date: 2026-01-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add attending_name column to call_assignments
    op.add_column(
        'call_assignments',
        sa.Column('attending_name', sa.String(100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('call_assignments', 'attending_name')
