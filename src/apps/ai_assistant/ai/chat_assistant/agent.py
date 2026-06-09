from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from core.settings import get_settings

settings = get_settings()
chat_agent_model = ChatOpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL_NAME,
)

chat_assistant_agent = create_agent(chat_agent_model, checkpointer=InMemorySaver())


def get_agent_config(chat_id: str) -> RunnableConfig:
    return RunnableConfig(configurable={"thread_id": chat_id})
