"""将已有会话写入共享消息记录及其文件镜像。"""

from pathlib import Path

from server.conversations import backfill_conversation_memory

from . import CONSTANT


if __name__ == "__main__":
    print(CONSTANT.BACKFILL_COMPLETED.format(backfill_conversation_memory=backfill_conversation_memory(Path(__file__).resolve().parents[1] / 'data')))
