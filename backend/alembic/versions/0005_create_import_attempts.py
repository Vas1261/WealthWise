"""create import attempts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02 21:14:50.652682

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('import_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.String(length=36), nullable=True),
    sa.Column(
        'result',
        sa.Enum('current', 'history', 'failed', 'rejected', name='import_result', native_enum=False,
                create_constraint=False),
        nullable=False,
    ),
    sa.Column(
        'broker', sa.Enum('zerodha', 'groww', name='broker', native_enum=False, create_constraint=False), nullable=True
    ),
    sa.Column(
        'asset_class',
        sa.Enum('equity', 'mutual_fund', name='asset_class', native_enum=False, create_constraint=False),
        nullable=True,
    ),
    sa.Column('statement_date', sa.Date(), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('rows_succeeded', sa.Integer(), nullable=False),
    sa.Column('rows_failed', sa.Integer(), nullable=False),
    sa.Column('replaced_existing', sa.Boolean(), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("asset_class IN ('equity', 'mutual_fund')", name=op.f('ck_import_attempts_asset_class')),
    sa.CheckConstraint("broker IN ('zerodha', 'groww')", name=op.f('ck_import_attempts_broker')),
    sa.CheckConstraint(
        "result IN ('current', 'history', 'failed', 'rejected')", name=op.f('ck_import_attempts_import_result')
    ),
    sa.ForeignKeyConstraint(
        ['batch_id'], ['import_batches.id'], name=op.f('fk_import_attempts_batch_id_import_batches'),
        ondelete='SET NULL',
    ),
    sa.ForeignKeyConstraint(
        ['user_id'], ['users.id'], name=op.f('fk_import_attempts_user_id_users'), ondelete='CASCADE'
    ),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_attempts'))
    )
    with op.batch_alter_table('import_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_attempts_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('import_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_attempts_user_id'))

    op.drop_table('import_attempts')
