"""add card/deck content fields (arabic_text, audio_url, answer_verses, surah names)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-15 21:39:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('cards', sa.Column('arabic_text', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('audio_url', sa.String(), nullable=True))
    op.add_column('cards', sa.Column('answer_verses', sa.String(), nullable=True))
    op.add_column('decks', sa.Column('start_surah_name', sa.String(), nullable=True))
    op.add_column('decks', sa.Column('end_surah_name', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('cards', 'answer_verses')
    op.drop_column('cards', 'audio_url')
    op.drop_column('cards', 'arabic_text')
    op.drop_column('decks', 'end_surah_name')
    op.drop_column('decks', 'start_surah_name')
