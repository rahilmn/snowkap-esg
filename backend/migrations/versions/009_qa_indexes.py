"""Add missing indexes for production performance.

QA Audit 3: Missing indexes on frequently queried columns.

Revision ID: 009_qa_indexes
Revises: 008_v2_modules
"""
from typing import Sequence, Union

from alembic import op

revision: str = "009_qa_indexes"
down_revision: Union[str, None] = "008_v2_modules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # URL index for dedup checks — unique per tenant (news_tasks.py:46-52)
    op.create_index("ix_articles_tenant_url", "articles", ["tenant_id", "url"], unique=True, if_not_exists=True)
    # Relevance score for HOME/FEED filtering
    op.create_index("ix_articles_relevance_score", "articles", ["relevance_score"], if_not_exists=True)
    # Created_at for chronological sorting + decay queries
    op.create_index("ix_articles_created_at", "articles", ["created_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_articles_created_at")
    op.drop_index("ix_articles_relevance_score")
    op.drop_index("ix_articles_tenant_url")
