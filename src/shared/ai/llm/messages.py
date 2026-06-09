from langchain.messages import AIMessage, HumanMessage


class HumanMessageDTO(
    HumanMessage,
):  # as a base dto selected AIMessage from langchain.messages
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)


class AIMessageDTO(
    AIMessage,
):  # as a base dto selected AIMessage from langchain.messages
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
