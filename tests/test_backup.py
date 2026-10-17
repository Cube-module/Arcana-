"""Exercise actual pg_dump/pg_restore, then replay the external deletion registry."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from sqlalchemy import select, func
from app.models.orm import User, Card
from app.repositories.records import owner
from app.services.profile_service import deletion_token, delete_profile, apply_deletions


@pytest.mark.postgres
async def test_ac13_actual_backup_restore(db, tmp_path):
    root = Path(__file__).resolve().parent.parent
    local = root / ".work/postgresql/pgsql/bin"
    dump = shutil.which("pg_dump") or (str(local / "pg_dump.exe") if (local / "pg_dump.exe").exists() else None)
    restore = shutil.which("pg_restore") or (str(local / "pg_restore.exe") if (local / "pg_restore.exe").exists() else None)
    if not dump or not restore:
        pytest.skip("pg_dump and pg_restore are required for backup acceptance")
    url = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    file = tmp_path / "snapshot.dump"
    registry = tmp_path / "deletions.log"
    subprocess.run([dump, "--dbname", url, "-Fc", "-f", str(file)], check=True, capture_output=True)
    async with db.begin() as session:
        user = await owner(session, 101, lock=True)
        user_id = user.id
        await delete_profile(session, user, deletion_token(user), registry)
    await db.kw["bind"].dispose()
    subprocess.run([restore, "--dbname", url, "--clean", "--if-exists", "--single-transaction", str(file)], check=True, capture_output=True)
    async with db() as session:
        assert await session.get(User, user_id) is not None
    async with db.begin() as session:
        await apply_deletions(session, registry)
    async with db() as session:
        assert await session.get(User, user_id) is None
        assert await owner(session, 202)
        assert await session.scalar(select(func.count()).select_from(Card)) == 78
