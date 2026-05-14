"""add refresh_tokens table

Revision ID: 3be79eb4434a
Revises: 974dbc0404cc
Create Date: 2026-05-14 21:19:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '3be79eb4434a'
down_revision: Union[str, Sequence[str], None] = '974dbc0404cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'refresh_tokens',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token', sa.String(), unique=True, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_refresh_tokens_token', 'refresh_tokens', ['token'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_refresh_tokens_token', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
