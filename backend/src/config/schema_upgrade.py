"""演示环境的幂等轻量升级：create_all 不修改已存在的表，这里补齐重算版本所需的列。"""
from sqlalchemy import inspect,text
from src.config.database import engine,Base
from src import models  # noqa: F401  确保 create_all 能看到全部表
def ensure_schema():
    Base.metadata.create_all(engine)
    inspector=inspect(engine)
    additions={
        "comparison_runs":{"active_version_id":"INT NULL","recalc_generation":"INT NOT NULL DEFAULT 0"},
        "qualifications":{"version_id":"INT NULL"},
    }
    with engine.begin() as conn:
        for table,columns in additions.items():
            if not inspector.has_table(table):continue
            existing={c["name"] for c in inspector.get_columns(table)}
            for name,ddl in columns.items():
                if name not in existing:conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
