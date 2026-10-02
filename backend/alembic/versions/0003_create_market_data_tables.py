"""create market data tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02 20:39:09.062742

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('instrument_symbols',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('isin', sa.String(length=12), nullable=False),
    sa.Column(
        'status',
        sa.Enum('resolved', 'not_found', 'ambiguous', name='symbol_status', native_enum=False, create_constraint=False),
        nullable=False,
    ),
    sa.Column('symbol', sa.String(length=32), nullable=True),
    sa.Column('mic_code', sa.String(length=10), nullable=True),
    sa.Column('exchange', sa.String(length=32), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('instrument_name', sa.String(length=255), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status != 'resolved' OR (symbol IS NOT NULL AND mic_code IS NOT NULL)",
        name=op.f('ck_instrument_symbols_resolved_has_listing'),
    ),
    sa.CheckConstraint(
        "status IN ('resolved', 'not_found', 'ambiguous')", name=op.f('ck_instrument_symbols_symbol_status')
    ),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_instrument_symbols')),
    sa.UniqueConstraint('isin', name=op.f('uq_instrument_symbols_isin'))
    )
    op.create_table('market_prices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('mic_code', sa.String(length=10), nullable=False),
    sa.Column('price', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_market_prices')),
    sa.UniqueConstraint('symbol', 'mic_code', name='uq_market_prices_listing')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('market_prices')
    op.drop_table('instrument_symbols')
