"""add_server_defaults_to_recovery_cases

Revision ID: 741dcb481af8
Revises: c83868141224
Create Date: 2026-08-28 23:43:22.906077

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '741dcb481af8'
down_revision: Union[str, None] = 'c83868141224'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "recovery_cases", "status",
        server_default="CREATED", existing_type=sa.String(16), nullable=False,
    )
    op.alter_column(
        "recovery_cases", "currency",
        server_default="INR", existing_type=sa.String(8), nullable=False,
    )


def downgrade() -> None:
    op.alter_column("recovery_cases", "status",   server_default=None, existing_type=sa.String(16), nullable=False)
    op.alter_column("recovery_cases", "currency", server_default=None, existing_type=sa.String(8),  nullable=False)
