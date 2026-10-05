"""create finance entries and budgets

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Exact money: NUMERIC(20, 2) where the engine has a real decimal type; on SQLite (whose NUMERIC
# columns hold 64-bit floats) the canonical decimal text, read back as Decimal by the model's type.
MONEY = sa.Numeric(precision=20, scale=2).with_variant(sa.String(length=22), 'sqlite')
AMOUNT_POSITIVE = 'CAST(amount AS NUMERIC) > 0'

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('finance_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column(
        'kind',
        sa.Enum('income', 'expense', name='finance_kind', native_enum=False, create_constraint=False),
        nullable=False,
    ),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('amount', MONEY, nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(AMOUNT_POSITIVE, name=op.f('ck_finance_entries_amount_positive')),
    sa.CheckConstraint("kind IN ('income', 'expense')", name=op.f('ck_finance_entries_finance_kind')),
    sa.ForeignKeyConstraint(
        ['user_id'], ['users.id'], name=op.f('fk_finance_entries_user_id_users'), ondelete='CASCADE'
    ),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_finance_entries'))
    )
    with op.batch_alter_table('finance_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_finance_entries_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_finance_entries_user_id_entry_date', ['user_id', 'entry_date'], unique=False)

    op.create_table('budgets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('month', sa.Integer(), nullable=False),
    sa.Column('amount', MONEY, nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(AMOUNT_POSITIVE, name=op.f('ck_budgets_amount_positive')),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_budgets_month_range')),
    sa.CheckConstraint('year BETWEEN 1900 AND 9999', name=op.f('ck_budgets_year_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_budgets_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budgets')),
    sa.UniqueConstraint('user_id', 'year', 'month', 'category', name='uq_budgets_user_period_category')
    )
    with op.batch_alter_table('budgets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_budgets_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('budgets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_budgets_user_id'))

    op.drop_table('budgets')
    with op.batch_alter_table('finance_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_finance_entries_user_id_entry_date')
        batch_op.drop_index(batch_op.f('ix_finance_entries_user_id'))

    op.drop_table('finance_entries')
