"""stage2: editorial workflow, homepage control, data section, ads/author/category extensions

Additive only: new tables + new columns that all carry server defaults, so existing rows are preserved.
Data change: article_type "data-story" is renamed "data-report" (reversible).

Revision ID: 3bca93f6297f
Revises: 86462bd9bf17
Create Date: 2026-10-20 10:04:08.886221

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3bca93f6297f'
down_revision = '86462bd9bf17'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('datasets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('topic', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('unit', sa.String(length=80), nullable=False),
    sa.Column('frequency', sa.String(length=12), nullable=False),
    sa.Column('decimals', sa.Integer(), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('context', sa.Text(), nullable=False),
    sa.Column('published', sa.Boolean(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_datasets')),
    sa.UniqueConstraint('slug', name=op.f('uq_datasets_slug'))
    )
    op.create_table('reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('topic', sa.String(length=60), nullable=False),
    sa.Column('file_url', sa.String(length=600), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('published_on', sa.Date(), nullable=True),
    sa.Column('published', sa.Boolean(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports'))
    )
    op.create_table('data_points',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dataset_id', sa.Integer(), nullable=False),
    sa.Column('period', sa.Date(), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], name=op.f('fk_data_points_dataset_id_datasets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_points')),
    sa.UniqueConstraint('dataset_id', 'period', name='uq_data_points_dataset_period')
    )
    with op.batch_alter_table('data_points', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_points_dataset_id'), ['dataset_id'], unique=False)

    op.create_table('home_sections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('article_type', sa.String(length=60), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('visible', sa.Boolean(), nullable=False),
    sa.Column('item_limit', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_home_sections_category_id_categories'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_home_sections'))
    )
    op.create_table('article_revisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('article_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('user_name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('subtitle', sa.String(length=300), nullable=False),
    sa.Column('excerpt', sa.String(length=400), nullable=False),
    sa.Column('body_html', sa.Text(), nullable=False),
    sa.Column('sources_md', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_article_revisions_article_id_articles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_article_revisions_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_article_revisions'))
    )
    with op.batch_alter_table('article_revisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_article_revisions_article_id'), ['article_id'], unique=False)

    op.create_table('editorial_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('article_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('user_name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_editorial_notes_article_id_articles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_editorial_notes_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_editorial_notes'))
    )
    with op.batch_alter_table('editorial_notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_editorial_notes_article_id'), ['article_id'], unique=False)

    op.create_table('home_slots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slot', sa.String(length=8), nullable=False),
    sa.Column('article_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name=op.f('fk_home_slots_article_id_articles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_home_slots')),
    sa.UniqueConstraint('slot', 'article_id', name='uq_home_slots_slot_article')
    )
    with op.batch_alter_table('advertisements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('campaign', sa.String(length=160), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('impressions', sa.Integer(), nullable=False, server_default='0'))

    with op.batch_alter_table('articles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_breaking', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('sponsor_name', sa.String(length=160), nullable=False, server_default=''))

    with op.batch_alter_table('authors', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expertise', sa.String(length=300), nullable=False, server_default=''))

    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_categories_parent_id_categories'), 'categories', ['parent_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.String(length=300), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))


    # Data change (reversible): renamed article type
    op.execute("UPDATE articles SET article_type = 'data-report' WHERE article_type = 'data-story'")


def downgrade():
    op.execute("UPDATE articles SET article_type = 'data-story' WHERE article_type = 'data-report'")
    op.execute("UPDATE articles SET article_type = 'news' WHERE article_type = 'sponsored'")
    op.execute("UPDATE articles SET status = 'draft' WHERE status = 'archived'")
    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.drop_column('active')
        batch_op.drop_column('description')

    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_categories_parent_id_categories'), type_='foreignkey')
        batch_op.drop_column('parent_id')
        batch_op.drop_column('active')

    with op.batch_alter_table('authors', schema=None) as batch_op:
        batch_op.drop_column('expertise')

    with op.batch_alter_table('articles', schema=None) as batch_op:
        batch_op.drop_column('sponsor_name')
        batch_op.drop_column('is_breaking')

    with op.batch_alter_table('advertisements', schema=None) as batch_op:
        batch_op.drop_column('impressions')
        batch_op.drop_column('campaign')

    op.drop_table('home_slots')
    with op.batch_alter_table('editorial_notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_editorial_notes_article_id'))

    op.drop_table('editorial_notes')
    with op.batch_alter_table('article_revisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_article_revisions_article_id'))

    op.drop_table('article_revisions')
    op.drop_table('home_sections')
    with op.batch_alter_table('data_points', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_points_dataset_id'))

    op.drop_table('data_points')
    op.drop_table('reports')
    op.drop_table('datasets')
