"""将无归属的旧 task.json 导入指定账号：python -m server.migrate_conversations --owner-id 1。"""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from server import database
from server.conversations import ensure_table, read_conversation
from server.data_model import Conversation, User

from . import CONSTANT


DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def migrate_legacy_conversations(data_dir: Path, owner_id: int) -> int:
    ensure_table()
    imported = 0
    with database.get_session() as session:
        if session.get(User, owner_id) is None:
            raise ValueError(CONSTANT.MIGRATION_USER_NOT_FOUND.format(owner_id=owner_id))
        for path in (data_dir / "tasks").glob("*/task.json"):
            metadata = read_conversation(json.loads(path.read_text(encoding="utf-8")))
            if session.scalar(select(Conversation.id).where(Conversation.conversation_id == metadata["id"])) is not None:
                continue
            session.add(Conversation(
                conversation_id=metadata["id"], owner_id=owner_id,
                directory_name=path.parent.name, metadata_json=metadata,
            ))
            session.flush()
            imported += 1
        session.commit()
    return imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=CONSTANT.MIGRATION_DESCRIPTION)
    parser.add_argument("--owner-id", type=int, required=True)
    arguments = parser.parse_args()
    print(CONSTANT.MIGRATION_COMPLETED.format(migrate_legacy_conversations=migrate_legacy_conversations(DATA_DIR, arguments.owner_id)))
