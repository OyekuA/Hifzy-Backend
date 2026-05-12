"""add given_name, family_name, picture, email_verified to users

Revision ID: be1f75588217
Revises: be1f75588216
Create Date: 2026-05-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "be1f75588217"
down_revision: Union[str, Sequence[str], None] = "be1f75588216"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("given_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("family_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("picture", sa.String(), nullable=True))
    op.add_column(
        "users", sa.Column("email_verified", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified")
    op.drop_column("users", "picture")
    op.drop_column("users", "family_name")
    op.drop_column("users", "given_name")
