"""Meal plans hold dishes, not recipes, and stock entries can be removed.

`meal_plans.recipe_id` becomes nullable so a meal can consist only of typed
dishes. SQLite cannot relax NOT NULL in place and adding a column would change
the table's column list, which the upgrade audit treats as data drift, so the
table is rebuilt with identical column names, order and values. Dishes live in
a new table; existing meals keep their recipe through the legacy column.
"""
from alembic import op

revision = '0015_meal_dishes'
down_revision = '0014_epub_documents'
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    if c.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Use verified maintenance upgrade')
    op.execute("""CREATE TABLE meal_plans_new (
        id TEXT PRIMARY KEY NOT NULL, local_date TEXT NOT NULL,
        slot TEXT NOT NULL CHECK(slot IN ('breakfast','lunch','dinner')),
        recipe_id TEXT REFERENCES recipes(resource_id) ON DELETE RESTRICT,
        servings INTEGER NOT NULL CHECK(servings BETWEEN 1 AND 100), note TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        deleted_at TEXT)""")
    op.execute("""INSERT INTO meal_plans_new(id,local_date,slot,recipe_id,servings,note,version,created_at,updated_at,deleted_at)
        SELECT id,local_date,slot,recipe_id,servings,note,version,created_at,updated_at,deleted_at
        FROM meal_plans ORDER BY id""")
    op.execute('DROP TABLE meal_plans')
    op.execute('ALTER TABLE meal_plans_new RENAME TO meal_plans')
    op.execute('CREATE INDEX ix_meal_date ON meal_plans(local_date,deleted_at,slot,id)')
    op.execute('CREATE INDEX ix_meal_recipe ON meal_plans(recipe_id,local_date)')

    # One row per dish. Either it points at a recipe or carries a typed label,
    # never both and never neither.
    op.execute("""CREATE TABLE meal_dishes (
        id TEXT PRIMARY KEY NOT NULL,
        meal_id TEXT NOT NULL REFERENCES meal_plans(id) ON DELETE CASCADE,
        recipe_id TEXT REFERENCES recipes(resource_id) ON DELETE RESTRICT,
        label TEXT NOT NULL DEFAULT '' CHECK(length(label)<=200),
        position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0),
        CHECK((recipe_id IS NOT NULL AND label='') OR (recipe_id IS NULL AND length(trim(label))>0)))""")
    op.execute('CREATE INDEX ix_meal_dishes ON meal_dishes(meal_id,position,id)')
    op.execute('CREATE INDEX ix_meal_dishes_recipe ON meal_dishes(recipe_id)')


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
