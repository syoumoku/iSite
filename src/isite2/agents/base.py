from __future__ import annotations

from typing import Generic, Protocol, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class Agent(Protocol, Generic[InputT, OutputT]):
    key: str

    def run(self, input_data: InputT) -> OutputT:
        ...


class AgentStep(Protocol, Generic[InputT, OutputT]):
    key: str
    input_model: str
    output_model: str

    def run(self, input_data: InputT) -> OutputT:
        ...
