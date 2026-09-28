"""One-off RDS role setup. Run before migrations while all app services are stopped."""
import os
from pathlib import Path

import psycopg
from psycopg import sql


def configure_roles(connection, migration_password, runtime_password):
    # No role has SUPERUSER, CREATEDB or CREATEROLE. Re-running rotates supplied
    # passwords deliberately; coordinate secret versions with stopped services.
    with connection.transaction():
        for role, password in (("convoy_migrator", migration_password), ("convoy_app", runtime_password)):
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            flags = connection.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=%s",
                (role,),
            ).fetchone()
            if any(flags):
                raise RuntimeError("refusing to repurpose an elevated database role")
            # Even NOSUPERUSER requires an actual superuser. RDS's master is
            # deliberately not one; CREATE ROLE defaults plus inspection above
            # enforce the boundary without requesting superuser-only changes.
            connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}")
                               .format(sql.Identifier(role), sql.Literal(password)))
        # RDS's administrative role may administer objects, but PostgreSQL 17's
        # SET ROLE checks still require membership for ownership/default grants.
        admin = connection.execute("SELECT current_user").fetchone()[0]
        connection.execute(sql.SQL("GRANT convoy_migrator TO {} WITH SET TRUE")
                           .format(sql.Identifier(admin)))
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE, CREATE ON SCHEMA public TO convoy_migrator")
        connection.execute("GRANT USAGE ON SCHEMA public TO convoy_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO convoy_app")
        connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO convoy_app")
        connection.execute("ALTER DEFAULT PRIVILEGES FOR ROLE convoy_migrator IN SCHEMA public "
                           "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO convoy_app")
        connection.execute("ALTER DEFAULT PRIVILEGES FOR ROLE convoy_migrator IN SCHEMA public "
                           "GRANT USAGE, SELECT ON SEQUENCES TO convoy_app")


def main():
    with psycopg.connect(host=os.environ["PGHOST"], port=5432, dbname=os.environ["PGDATABASE"],
                         user="convoy_admin", password=os.environ.pop("CONVOY_MASTER_PASSWORD"),
                         sslmode="verify-full", sslrootcert=str(Path(__file__).with_name("rds-ca.pem")),
                         connect_timeout=5) as connection:
        configure_roles(connection, os.environ.pop("CONVOY_MIGRATION_DB_PASSWORD"),
                        os.environ.pop("CONVOY_RUNTIME_DB_PASSWORD"))
    print("Database roles configured; run the separate migration task next.")


if __name__ == "__main__":
    main()
