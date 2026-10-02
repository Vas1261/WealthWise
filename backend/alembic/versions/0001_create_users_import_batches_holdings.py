"""create users, import_batches, holdings

Revision ID: 0001
Revises: 
Create Date: 2026-10-02 16:51:52.065364

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('reporting_currency', sa.String(length=3), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('import_batches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('broker', sa.Enum('zerodha', 'groww', name='broker', native_enum=False, create_constraint=False), nullable=False),
    sa.Column('asset_class', sa.Enum('equity', 'mutual_fund', name='asset_class', native_enum=False, create_constraint=False), nullable=False),
    sa.Column('statement_date', sa.Date(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rows_succeeded', sa.Integer(), nullable=False),
    sa.Column('rows_failed', sa.Integer(), nullable=False),
    sa.CheckConstraint("asset_class IN ('equity', 'mutual_fund')", name=op.f('ck_import_batches_asset_class')),
    sa.CheckConstraint("broker IN ('zerodha', 'groww')", name=op.f('ck_import_batches_broker')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_import_batches_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batches')),
    sa.UniqueConstraint('user_id', 'broker', 'asset_class', 'statement_date', name='uq_import_batches_snapshot_identity')
    )

    op.create_table('holdings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_batch_id', sa.String(length=36), nullable=False),
    sa.Column('instrument_name', sa.String(length=255), nullable=False),
    sa.Column('isin', sa.String(length=12), nullable=True),
    sa.Column('symbol', sa.String(length=64), nullable=True),
    sa.Column('asset_class', sa.Enum('equity', 'mutual_fund', name='asset_class', native_enum=False, create_constraint=False), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('average_buy_price', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('native_currency', sa.String(length=3), nullable=False),
    sa.Column('source_broker', sa.Enum('zerodha', 'groww', name='broker', native_enum=False, create_constraint=False), nullable=False),
    sa.CheckConstraint("asset_class IN ('equity', 'mutual_fund')", name=op.f('ck_holdings_asset_class')),
    sa.CheckConstraint("source_broker IN ('zerodha', 'groww')", name=op.f('ck_holdings_broker')),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], name=op.f('fk_holdings_import_batch_id_import_batches'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_holdings'))
    )
    with op.batch_alter_table('holdings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_holdings_import_batch_id'), ['import_batch_id'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('holdings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_holdings_import_batch_id'))

    op.drop_table('holdings')
    op.drop_table('import_batches')
    op.drop_table('users')
