"""Create the first super admin.

There is no public registration and no API that lets someone grant themselves
a privileged role. Run this once, from an operator shell, before anyone can
sign in:

    PowerShell:
        $env:ADP_BOOTSTRAP_EMAIL = "admin@example.com"
        $env:ADP_BOOTSTRAP_PASSWORD = "a-long-unique-password"
        python -m app.bootstrap
        Remove-Item Env:ADP_BOOTSTRAP_PASSWORD

The password is read from the environment, not from the command line, and it
is stored only as a scrypt hash. The command refuses to run when a super
admin already exists. After signing in, change the password and unset the
environment variable. Further accounts and roles are created by a signed-in
super admin.
"""

import os
import sys

from app.config import Settings
from app.db import init_database, make_engine, make_session_factory
from app.domain import create_initial_super_admin
from app.errors import AppError


def main() -> int:
    email = os.environ.get("ADP_BOOTSTRAP_EMAIL", "")
    password = os.environ.get("ADP_BOOTSTRAP_PASSWORD", "")
    if not email or not password:
        print("Set ADP_BOOTSTRAP_EMAIL and ADP_BOOTSTRAP_PASSWORD, then run this command again.", file=sys.stderr)
        return 1
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    init_database(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        try:
            user = create_initial_super_admin(db, email, password)
        except AppError as exc:
            print(exc.detail, file=sys.stderr)
            return 1
    print(f"Super admin created for {user.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
