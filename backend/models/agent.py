"""Agent model — CRUD operations backed by agents.json config file."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from backend.config import AGENTS_FILE, DEFAULT_AGENT, CLAUDE_AGENT

logger = logging.getLogger(__name__)


@dataclass
class Agent:
    """Represents a configured ACP agent."""
    id: str
    name: str
    type: str = "cli"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        logger.debug("Serializing agent: id=%s name=%s", self.id, self.name)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        logger.debug("Deserializing agent: id=%s", data.get("id", "unknown"))
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            type=data.get("type", "cli"),
            command=data.get("command", ""),
            args=data.get("args", []),
            env_vars=data.get("env_vars", {}),
            description=data.get("description", ""),
        )


class AgentStore:
    """Persistent store for agent configurations.

    Reads and writes to ~/.pi/acp-chat-app/agents.json.
    On first access, seeds with the default pi-agent if the file doesn't exist.
    """

    def __init__(self, file_path: Path | None = None) -> None:
        self._file_path = file_path or AGENTS_FILE
        logger.debug("AgentStore initialized with path: %s", self._file_path)

    def _read(self) -> list[dict[str, Any]]:
        """Read agents from the JSON file."""
        logger.debug("Reading agents from: %s", self._file_path)
        if not self._file_path.exists():
            logger.debug("Agents file not found, seeding with default agent")
            self._seed_default()
            return self._read()
        try:
            data = json.loads(self._file_path.read_text())
            agents = data.get("agents", [])
            logger.debug("Read %d agents from file", len(agents))
            return agents
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("Error reading agents file: %s, returning empty list", e)
            return []

    def _write(self, agents: list[dict[str, Any]]) -> None:
        """Write agents to the JSON file."""
        logger.debug("Writing %d agents to: %s", len(agents), self._file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps({"agents": agents}, indent=2))

    def _seed_default(self) -> None:
        """Seed the agents file with default agents."""
        logger.debug("Seeding default agents")
        self._write([dict(DEFAULT_AGENT), dict(CLAUDE_AGENT)])

    def list_all(self) -> list[Agent]:
        """List all configured agents."""
        logger.debug("Listing all agents")
        agents = [Agent.from_dict(a) for a in self._read()]
        logger.debug("Found %d agents", len(agents))
        return agents

    def get(self, agent_id: str) -> Agent | None:
        """Get an agent by ID."""
        logger.debug("Getting agent: %s", agent_id)
        for data in self._read():
            if data.get("id") == agent_id:
                logger.debug("Found agent: %s", agent_id)
                return Agent.from_dict(data)
        logger.debug("Agent not found: %s", agent_id)
        return None

    def create(self, agent: Agent) -> Agent:
        """Create a new agent. Generates an ID if not provided."""
        logger.debug("Creating agent: name=%s", agent.name)
        if not agent.id:
            agent.id = str(uuid.uuid4())
        agents = self._read()
        agents.append(agent.to_dict())
        self._write(agents)
        logger.debug("Created agent: id=%s name=%s", agent.id, agent.name)
        return agent

    def update(self, agent: Agent) -> Agent | None:
        """Update an existing agent. Returns None if not found."""
        logger.debug("Updating agent: id=%s", agent.id)
        agents = self._read()
        for i, data in enumerate(agents):
            if data.get("id") == agent.id:
                agents[i] = agent.to_dict()
                self._write(agents)
                logger.debug("Updated agent: id=%s", agent.id)
                return agent
        logger.debug("Agent not found for update: %s", agent.id)
        return None

    def delete(self, agent_id: str) -> bool:
        """Delete an agent by ID. Returns True if deleted, False if not found."""
        logger.debug("Deleting agent: %s", agent_id)
        agents = self._read()
        for i, data in enumerate(agents):
            if data.get("id") == agent_id:
                del agents[i]
                self._write(agents)
                logger.debug("Deleted agent: %s", agent_id)
                return True
        logger.debug("Agent not found for delete: %s", agent_id)
        return False
