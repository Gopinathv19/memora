from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.database import Base

# Importing the models package registers every table on Base.metadata, which
# is what `alembic revision --autogenerate` diffs against the live database.
import app.db.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url",get_settings().database_url)

target_metadata = Base.metadata
# The URL comes from the environment, never from alembic.ini, so no connection
# string with a password ever lands in a committed file.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


# Indexes created with raw DDL in a migration because the ORM cannot express
# them. Autogenerate would otherwise see them as stray and emit a DROP INDEX.
#   ix_chunk_embeddings_embedding_hnsw -- HNSW over embedding::halfvec(2048)
#   (migration d4e5f6a7b8c9)
MIGRATION_ONLY_INDEXES = frozenset({"ix_chunk_embeddings_embedding_hnsw"})


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "index" and name in MIGRATION_ONLY_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
