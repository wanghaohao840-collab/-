from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from app.qa_models import QaConversationAggregate, QaMessage
from app.qa_repository import QaRepository


def qa_memory_id(user_id: str, assistant_message_id: str) -> str:
    return f"qa-{uuid5(NAMESPACE_URL, f'{user_id}:{assistant_message_id}')}"


class QaMemoryLinker:
    def __init__(self, repository: QaRepository) -> None:
        self.repository = repository

    def sync(
        self,
        runtime,
        conversation: QaConversationAggregate,
        message: QaMessage,
        worker_id: str,
        *,
        now: str | None = None,
    ) -> bool:
        memory_id = qa_memory_id(message.user_id, message.id)
        manager = runtime.memory_tool.memory_manager
        try:
            manager.add_memory(
                message.content,
                memory_type="episodic",
                importance=0.7,
                metadata={
                    "conversation_id": conversation.id,
                    "qa_message_id": message.id,
                    "document_ids": [
                        item.document_id for item in conversation.documents
                    ],
                    "event_type": "pdf_qa",
                    "session_id": conversation.id,
                },
                memory_id=memory_id,
            )
        except Exception:
            self.repository.fail_memory_sync(
                message.user_id,
                message.id,
                worker_id,
                message.version,
                "QA_MEMORY_WRITE_FAILED",
                now=now,
            )
            return False

        attached = self.repository.complete_memory_sync(
            message.user_id,
            message.id,
            worker_id,
            memory_id,
            message.version,
            now=now,
        )
        if not attached:
            manager.remove_memory(memory_id, memory_type="episodic")
        return attached
