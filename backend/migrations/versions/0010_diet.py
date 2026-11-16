"""Recipes, explicit meal plans, independent shopping and manual stock balances."""
from alembic import op
revision = '0010_diet'
down_revision = '0009_project_tools'
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    if c.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Use verified maintenance upgrade')
    if c.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='resources'").first():
        raise RuntimeError('Unrecognized resource triggers')
    indexes=c.exec_driver_sql("SELECT name,sql FROM sqlite_schema WHERE type='index' AND tbl_name='resources' AND sql IS NOT NULL").fetchall()
    with op.batch_alter_table('resources', recreate='always') as b:
        b.drop_constraint('resource_kind', type_='check')
        b.create_check_constraint('resource_kind', "kind IN ('task','event','note','collection','document','book','bookmark','course','learning_goal','learning_path','knowledge_node','project','recipe')")
    for name, sql in indexes:
        c.exec_driver_sql('DROP INDEX IF EXISTS "'+name.replace('"','""')+'"')
        c.exec_driver_sql(sql)
    for sql in [
        """CREATE TABLE diet_ingredients (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
          normalized_name TEXT NOT NULL UNIQUE, version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE recipes (resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
          instructions TEXT NOT NULL DEFAULT '', base_servings INTEGER NOT NULL CHECK(base_servings BETWEEN 1 AND 100),
          favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)))""",
        """CREATE TABLE recipe_ingredients (id TEXT PRIMARY KEY NOT NULL,
          recipe_id TEXT NOT NULL REFERENCES recipes(resource_id) ON DELETE RESTRICT,
          ingredient_id TEXT NOT NULL REFERENCES diet_ingredients(id) ON DELETE RESTRICT,
          quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0 AND quantity_milli<=1000000000),
          unit TEXT NOT NULL CHECK(unit IN ('g','kg','ml','l','piece','portion')),
          position INTEGER NOT NULL CHECK(position>=0), created_at TEXT NOT NULL,
          UNIQUE(recipe_id,position))""",
        'CREATE INDEX ix_recipe_ingredient ON recipe_ingredients(ingredient_id,recipe_id)',
        """CREATE TABLE meal_plans (id TEXT PRIMARY KEY NOT NULL, local_date TEXT NOT NULL,
          slot TEXT NOT NULL CHECK(slot IN ('breakfast','lunch','dinner')),
          recipe_id TEXT NOT NULL REFERENCES recipes(resource_id) ON DELETE RESTRICT,
          servings INTEGER NOT NULL CHECK(servings BETWEEN 1 AND 100), note TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          deleted_at TEXT)""",
        'CREATE INDEX ix_meal_date ON meal_plans(local_date,deleted_at,slot,id)',
        'CREATE INDEX ix_meal_recipe ON meal_plans(recipe_id,local_date)',
        """CREATE TABLE shopping_lists (id TEXT PRIMARY KEY NOT NULL, week_start TEXT NOT NULL UNIQUE,
          version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE shopping_items (id TEXT PRIMARY KEY NOT NULL,
          list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE RESTRICT,
          ingredient_id TEXT NOT NULL REFERENCES diet_ingredients(id) ON DELETE RESTRICT,
          quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0 AND quantity_milli<=1000000000000),
          unit TEXT NOT NULL CHECK(unit IN ('g','kg','ml','l','piece','portion')),
          note TEXT NOT NULL DEFAULT '', purchased INTEGER NOT NULL DEFAULT 0 CHECK(purchased IN (0,1)),
          origin TEXT NOT NULL CHECK(origin IN ('manual','menu')), source_digest TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, removed_at TEXT)""",
        'CREATE INDEX ix_shopping_items ON shopping_items(list_id,removed_at,created_at,id)',
        """CREATE TABLE inventory_balances (id TEXT PRIMARY KEY NOT NULL,
          ingredient_id TEXT NOT NULL REFERENCES diet_ingredients(id) ON DELETE RESTRICT,
          unit TEXT NOT NULL CHECK(unit IN ('g','ml','piece','portion')),
          quantity_milli INTEGER NOT NULL CHECK(quantity_milli>=0 AND quantity_milli<=1000000000),
          version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(ingredient_id,unit))""",
    ]:
        c.exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Restore verified full snapshot and matching application')
