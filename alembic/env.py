from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

from alembic import context

# Settings own the single source of truth for the DB URL (a SQLAlchemy URL object,
# not a string — special chars in the password are escaped by URL.create).
# Importing each module's models below registers their tables on SQLModel.metadata.
# env.py is migration infrastructure, not a domain module — it is the one place
# allowed to import every module. Add a line per table-owning module.
from auth import models as _auth_models  # noqa: F401
from auth.webauthn import models as _webauthn_models  # noqa: F401
from config import get_settings
from email_infra import models as _email_models  # noqa: F401
from measurements import models as _measurements_models  # noqa: F401
from prescriptions import models as _prescriptions_models  # noqa: F401
from reminders import models as _reminders_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection (alembic ... --sql). Rarely needed early."""
    url = get_settings().database_url.render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Build the engine straight from the URL object. No set_main_option /
    # engine_from_config round-trip through the ini — that would stringify the URL
    # and expose the password to ConfigParser %-interpolation.
    connectable = create_engine(
        get_settings().database_url,
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
