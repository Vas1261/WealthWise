"""holdings: quantity > 0 and average_buy_price >= 0

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('holdings', schema=None) as batch_op:
        batch_op.create_check_constraint(op.f('ck_holdings_quantity_positive'), 'quantity > 0')
        batch_op.create_check_constraint(
            op.f('ck_holdings_average_buy_price_non_negative'), 'average_buy_price >= 0'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('holdings', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_holdings_average_buy_price_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_holdings_quantity_positive'), type_='check')
