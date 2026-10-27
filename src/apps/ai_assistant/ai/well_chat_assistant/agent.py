from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from apps.ai_assistant.ai.well_chat_assistant.prompt import render_system_prompt
from apps.ai_assistant.ai.well_chat_assistant.tools import WELL_CHAT_ASSISTANT_TOOLS
from core.settings import get_settings

settings = get_settings()


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


well_chat_agent_model = ChatOpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL_NAME,
)

well_chat_assistant_agent = create_agent(
    model=well_chat_agent_model,
    tools=WELL_CHAT_ASSISTANT_TOOLS,
    middleware=[well_system_prompt],
    context_schema=WellChatContext,
    checkpointer=InMemorySaver(),
)


def get_well_agent_config(thread_id: str, well_id: int | None) -> RunnableConfig:
    configurable: dict[str, object] = {"thread_id": thread_id}
    if well_id is not None:
        configurable["well_id"] = well_id
    return RunnableConfig(configurable=configurable)
