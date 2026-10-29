"""Общие middleware агентов чатов."""

from langchain.agents import AgentState
from langchain.agents.middleware import after_model
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from shared.ai.llm.thinking import strip_thinking

# Рассуждение модели в чатах выключено: по умолчанию сервер и так отвечает
# без него (проверено 29.10.2026 — тот же ответ за те же 2 с), а с ним ответ
# в 8 раз дольше и рассуждение попадает в текст.
NO_REASONING = {"reasoning_effort": "none"}


@after_model
def strip_model_thinking(state: AgentState, _runtime: Runtime) -> dict | None:
    """Метки рассуждения вырезаются из ответа модели: не уходят клиенту и не
    копятся в истории треда (сообщение с тем же id заменяет исходное)."""
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not isinstance(last.content, str):
        return None
    clean = strip_thinking(last.content)
    if clean == last.content:
        return None
    return {"messages": [last.model_copy(update={"content": clean})]}
