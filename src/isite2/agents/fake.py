from __future__ import annotations

from isite2.agents.base import AgentStep
from isite2.orchestrator.state import PipelineState


class FakeDiscoveryAgent(AgentStep[PipelineState, PipelineState]):
    key = "fake_discovery"
    input_model = "PipelineState"
    output_model = "PipelineState"

    def run(self, input_data: PipelineState) -> PipelineState:
        input_data.record(self.key, "completed", "MVP discovery handled by orchestrator fixture.")
        return input_data


class FakeEvidenceAgent(AgentStep[PipelineState, PipelineState]):
    key = "fake_evidence"
    input_model = "PipelineState"
    output_model = "PipelineState"

    def run(self, input_data: PipelineState) -> PipelineState:
        input_data.record(self.key, "completed", "MVP evidence fixtures attached.")
        return input_data
