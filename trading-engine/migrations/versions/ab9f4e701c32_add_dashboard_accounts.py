"""Store dashboard accounts beside their linked trading users."""

from alembic import op
import sqlalchemy as sa


revision = "ab9f4e701c32"
down_revision = "e8a0c2d4f6b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA auth")
    op.create_table(
        "dashboard_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(60), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "trading_user_id",
            sa.BigInteger(),
            sa.ForeignKey("public.users.id", name="fk_dashboard_accounts_trading_user"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_dashboard_accounts_username",
        ),
        sa.CheckConstraint(
            "password_hash ~ '^[$]2[aby][$](1[0-6])[$][./A-Za-z0-9]{53}$'",
            name="ck_dashboard_accounts_password_hash",
        ),
        sa.CheckConstraint("trading_user_id > 0", name="ck_dashboard_accounts_user_id"),
        schema="auth",
    )


def downgrade() -> None:
    op.drop_table("dashboard_accounts", schema="auth")
    op.execute("DROP SCHEMA auth")
