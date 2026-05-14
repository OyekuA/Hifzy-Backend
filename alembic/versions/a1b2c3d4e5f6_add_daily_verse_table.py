"""add daily_verse table

Revision ID: a1b2c3d4e5f6
Revises: 3be79eb4434a
Create Date: 2026-05-14 21:19:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '3be79eb4434a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'daily_verse',
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('verse_key', sa.String(), nullable=False),
        sa.Column('arabic_text', sa.Text(), nullable=False),
        sa.Column('chapter_id', sa.Integer(), nullable=False),
        sa.Column('verse_number', sa.Integer(), nullable=False),
        sa.Column('juz_number', sa.Integer(), nullable=True),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('daily_verse')
