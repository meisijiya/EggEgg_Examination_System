"""add adapted_payload_json column

Revision ID: 0002_add_adapted_payload
Revises: 66ef6d73a07c
Create Date: 2026-07-05 01:20:00.000000

fix-22 P0 critical bug 修复：为 attempt_answers 增加 adapted_payload_json 字段，
用于持久化混合模式 AI 改编 payload（含 adapted_answer），让判分用改编答案而非原题。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_adapted_payload"
down_revision: Union[str, Sequence[str], None] = "66ef6d73a07c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — 新增 adapted_payload_json 列（nullable，向后兼容）。"""
    op.add_column(
        "attempt_answers",
        sa.Column("adapted_payload_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("attempt_answers", "adapted_payload_json")