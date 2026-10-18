"""pin historical FX rates (FR-20): holding cost dates and a permanent historical rate table

Holdings already stored get no cost dates, so their cost is pinned at their statement date. Re-importing
a Groww mutual fund order history adds its purchase dates.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17 17:09:39.012657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('historical_fx_rates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('base', sa.String(length=3), nullable=False),
    sa.Column('quote', sa.String(length=3), nullable=False),
    sa.Column('requested_date', sa.Date(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=24, scale=12), nullable=False),
    sa.Column('rate_date', sa.Date(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_historical_fx_rates')),
    sa.UniqueConstraint('base', 'quote', 'requested_date', name='uq_historical_fx_rates_pair_date')
    )
    op.create_table('holding_cost_dates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('holding_id', sa.Integer(), nullable=False),
    sa.Column('on_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_holding_cost_dates_amount_positive')),
    sa.ForeignKeyConstraint(
        ['holding_id'], ['holdings.id'], name=op.f('fk_holding_cost_dates_holding_id_holdings'), ondelete='CASCADE'
    ),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_holding_cost_dates'))
    )
    with op.batch_alter_table('holding_cost_dates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_holding_cost_dates_holding_id'), ['holding_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('holding_cost_dates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_holding_cost_dates_holding_id'))

    op.drop_table('holding_cost_dates')
    op.drop_table('historical_fx_rates')
