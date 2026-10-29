from dataclasses import dataclass

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelRequest, before_model, dynamic_prompt
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from apps.ai_assistant.ai.middleware import NO_REASONING, strip_model_thinking
from apps.ai_assistant.ai.well_chat_assistant.prompt import render_system_prompt
from apps.ai_assistant.ai.well_chat_assistant.tools import WELL_CHAT_ASSISTANT_TOOLS
from core.settings import get_settings

settings = get_settings()

# Окно модели ~16k токенов: системный промпт с досье ~2.7k, схемы tools ~2.6k,
# ответ ~2k — на историю треда остаётся около 9k. Берём 7k с запасом.
HISTORY_MAX_TOKENS = 7000
# Русский текст и JSON — 1.7–2.6 символа на токен (cl100k), а не 4 по умолчанию.
CHARS_PER_TOKEN = 2.0


@dataclass(frozen=True, slots=True)
class WellChatContext:
    """Контекст одного вопроса: досье привязанной скважины.

    Досье живёт в контексте, а не в сообщениях: в историю треда оно не
    попадает и не раздувает окно, а на каждый вопрос подставляется свежее.
    """

    well_bound: bool = False
    dossier: str | None = None


@dynamic_prompt
def well_system_prompt(request: ModelRequest) -> str:
    context = request.runtime.context
    if not isinstance(context, WellChatContext):
        context = WellChatContext()
    return render_system_prompt(context.dossier, well_bound=context.well_bound)


def _count_tokens(messages: list[BaseMessage]) -> int:
    return count_tokens_approximately(messages, chars_per_token=CHARS_PER_TOKEN)


def trim_history(
    messages: list[BaseMessage],
    max_tokens: int = HISTORY_MAX_TOKENS,
) -> list[BaseMessage] | None:
    """Хвост треда, который влезает в бюджет; None — обрезать не нужно.

    Текущий ход (последний вопрос и ответы tools на него) остаётся целиком,
    старые ходы отбрасываются с начала; история начинается с вопроса
    пользователя, чтобы ответ tool не остался без своего вызова.
    """
    if _count_tokens(messages) <= max_tokens:
        return None
    last_question = max(
        (i for i, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )
    current = messages[last_question:]
    budget = max_tokens - _count_tokens(current)
    history = (
        trim_messages(
            messages[:last_question],
            max_tokens=budget,
            strategy="last",
            token_counter=_count_tokens,
            start_on="human",
        )
        if budget > 0
        else []
    )
    return [*history, *current]


@before_model
def trim_thread_history(state: AgentState, _runtime: Runtime) -> dict | None:
    """Старые ходы удаляются из треда насовсем: окно не переполняется, а
    InMemorySaver не растёт бесконечно."""
    kept = trim_history(state["messages"])
    if kept is None:
        return None
    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept]}


well_chat_agent_model = ChatOpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL_NAME,
    extra_body=NO_REASONING,
)

well_chat_assistant_agent = create_agent(
    model=well_chat_agent_model,
    tools=WELL_CHAT_ASSISTANT_TOOLS,
    middleware=[trim_thread_history, well_system_prompt, strip_model_thinking],
    context_schema=WellChatContext,
    checkpointer=InMemorySaver(),
)


def get_well_agent_config(thread_id: str, well_id: int | None) -> RunnableConfig:
    configurable: dict[str, object] = {"thread_id": thread_id}
    if well_id is not None:
        configurable["well_id"] = well_id
    return RunnableConfig(configurable=configurable)
